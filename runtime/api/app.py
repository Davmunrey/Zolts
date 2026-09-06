"""The API.

Every route resolves a tenant from the API key and does all its work inside a
tenant-scoped transaction. There is no route that takes a tenant id as a
parameter: the only way to name a tenant is to hold one of its keys.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import jsonschema
from fastapi import FastAPI, HTTPException, Query, Request, Response, status

from runtime.api import auth, console, webhooks
from runtime.api.signin import SIGN_IN_CSP, sign_in_page
from runtime.api.auth import CurrentPrincipal, Principal
from runtime.api.schemas import (AccountIn, EnrollmentOut, HealthOut, IngestOut,
                                 MeasurementOut, PersonIn, ProgramIn, ProgramOut,
                                 KeyIn, SessionIn, SignalIn, SignupIn)
from runtime.api.throttle import Throttle, caller_of
from runtime.connectors import install_default_connectors, providers_for
from runtime.db import Database
from runtime.engine import enroll
from runtime.repo import (actions, enrollments, entities, ledger, mappings, programs,
                          proposals)
from runtime.surface import content_security_policy, document, inject
from zolts import dsl, experiment

SURFACE = Path(__file__).resolve().parent.parent.parent / "design" / "console.html"


def _triage_factory():
    """How the webhook receiver reaches the agent layer, or does not.

    Both halves are required together, as everywhere else: a model with no
    spend guard generates against no ceiling, and a guard with no model has
    nothing to price. Absent either, the receiver gets None and a reply is
    recorded exactly as it was before the agent layer existed.
    """
    if os.environ.get("ZOLTS_AGENTS", "false").lower() != "true":
        return None
    try:
        from runtime.agents.client import ModelClient
        from runtime.agents.spend import SpendGuard
    except Exception:  # noqa: BLE001 - the API must start without the agent layer
        return None

    client, guard = ModelClient(), SpendGuard()
    if not guard.available:
        return None

    def factory(_tenant_id: str):
        # The per-tenant budget the guard prices against. Absent a stored
        # ceiling the guard answers `cannot-tell` and the call is refused,
        # which is the fail-closed direction.
        return client, guard, {"consumed_usd": None, "limit_usd": None}

    return factory


def create_app(db: Database, *, install_connectors: bool = True,
               secret_key: str | None = None) -> FastAPI:
    app = FastAPI(title="Zolts", version="0.1.0",
                  description="The GTM runtime: signal in, gated action out.")
    app.state.db = db
    app.state.secret_key = secret_key or os.environ.get("ZOLTS_SECRET_KEY", "")
    if install_connectors:
        install_default_connectors()
    # Webhooks authenticate by signature rather than by API key, so they are
    # mounted outside the key-protected surface.
    app.include_router(webhooks.router(db, app.state.secret_key), prefix="/v1")

    # -- health ----------------------------------------------------------

    signup_throttle = Throttle()
    app.state.signup_throttle = signup_throttle
    app.state.triage_factory = _triage_factory()

    def _throttle(request: Request) -> None:
        caller = caller_of(request)
        if not signup_throttle.allow(caller):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "too many signup attempts; try again shortly",
                headers={"retry-after": str(signup_throttle.retry_after(caller))})

    @app.get("/health", response_model=HealthOut)
    def health() -> HealthOut:
        migrations: list[str] = []
        ok = True
        try:
            with db.admin_tx() as cur:
                cur.execute("select version from schema_migration order by 1")
                migrations = [r["version"] for r in cur.fetchall()]
        except Exception:  # noqa: BLE001 - health must report, not raise
            ok = False
        return HealthOut(
            status="ok" if ok else "degraded", database=ok, migrations=migrations,
            isolation_enforced=db.isolation_enforced,
            connectors=sorted({p for c in ("email", "task", "crm") for p in providers_for(c)}))

    @app.get("/health/liveness")
    def liveness() -> dict[str, Any]:
        """Whether this deployment is doing its job, not whether it is up.

        Deliberately not tenant-scoped and deliberately counts only: it answers
        an operator's question, and returning a tenant's identifiers on an
        unauthenticated path would answer a different one.
        """
        from runtime import liveness as liveness_module

        try:
            report = liveness_module.check(db)
        except Exception as exc:  # noqa: BLE001 - a monitor must get an answer
            return {"draining": False, "signals": [
                {"name": "database", "ok": False, "detail": str(exc), "value": 0}]}
        return report.as_dict()

    # -- keys ------------------------------------------------------------

    @app.get("/v1/keys")
    def list_keys(principal: Principal = CurrentPrincipal) -> list[dict[str, Any]]:
        """Prefixes and usage, never tokens. A token is shown once, at creation."""
        from runtime.provision import list_api_keys

        return list_api_keys(db, principal.tenant_id)

    @app.post("/v1/keys", status_code=status.HTTP_201_CREATED)
    def create_key(body: KeyIn, principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        principal.require("admin")
        from runtime.provision import issue_api_key

        issued = issue_api_key(db, principal.tenant_id, body.name, body.scopes or [])
        with db.tenant_tx(principal.tenant_id) as cur:
            ledger.audit(cur, principal.tenant_id, actor=f"key:{principal.key_id}",
                         action="api_key.created", subject=issued.key_id,
                         detail={"name": body.name, "scopes": body.scopes or []})
        return {"id": issued.key_id, "prefix": issued.prefix, "token": issued.token,
                "note": "the token is shown once and is not recoverable"}

    @app.post("/v1/keys/{key_id}/rotate")
    def rotate_key(key_id: str, principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """A replacement is issued before the original is revoked.

        Revoking first leaves a window with no working key, and a rotation that
        causes an outage is one nobody performs a second time.
        """
        principal.require("admin")
        from runtime.provision import rotate_api_key

        try:
            issued = rotate_api_key(db, principal.tenant_id, key_id)
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                "no live key with that id") from exc
        with db.tenant_tx(principal.tenant_id) as cur:
            ledger.audit(cur, principal.tenant_id, actor=f"key:{principal.key_id}",
                         action="api_key.rotated", subject=issued.key_id,
                         detail={"replaced": key_id})
        return {"id": issued.key_id, "prefix": issued.prefix, "token": issued.token,
                "replaced": key_id,
                "note": "the token is shown once; the replaced key no longer works"}

    @app.delete("/v1/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
    def revoke_key(key_id: str, principal: Principal = CurrentPrincipal) -> Response:
        """Revoking is idempotent and never deletes the row: `last_used_at`
        still answers "was this key used after it leaked"."""
        principal.require("admin")
        from runtime.provision import revoke_api_key

        if key_id == principal.key_id:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "this is the key making the request; rotate it instead, "
                                "or revoke it with another key")
        changed = revoke_api_key(db, principal.tenant_id, key_id)
        if changed:
            with db.tenant_tx(principal.tenant_id) as cur:
                ledger.audit(cur, principal.tenant_id, actor=f"key:{principal.key_id}",
                             action="api_key.revoked", subject=key_id, detail={})
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # -- billing -----------------------------------------------------------

    @app.get("/v1/billing/current")
    def current_period(principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """What this period has consumed, and what is left of it."""
        from runtime import metering

        with db.tenant_tx(principal.tenant_id) as cur:
            cur.execute("select * from tenant where id = %s", (principal.tenant_id,))
            tenant = cur.fetchone()
            period = metering.open_period(cur, tenant)
            budget = metering.allowance(cur, tenant)
            cur.execute(
                "select kind, sum(billed_credits) as credits, count(*) as events"
                " from cost_event where billing_period_id = %s group by kind"
                " order by credits desc", (period["id"],))
            by_kind = [{"kind": r["kind"], "credits": float(r["credits"] or 0),
                        "events": int(r["events"])} for r in cur.fetchall()]

        return {"plan": tenant["plan"],
                "periodStart": period["starts_at"], "periodEnd": period["ends_at"],
                "includedCredits": float(period["included_credits"]),
                "consumedCredits": float(budget.consumed),
                "remainingCredits": float(budget.remaining),
                "spending": budget.allowed,
                "byKind": by_kind}

    @app.get("/v1/billing/statements")
    def statements(principal: Principal = CurrentPrincipal) -> list[dict[str, Any]]:
        """Closed periods, newest first. A statement is what can be invoiced."""
        with db.tenant_tx(principal.tenant_id) as cur:
            cur.execute(
                "select id, starts_at, ends_at, statement from billing_period"
                " where closed_at is not null order by starts_at desc limit 24")
            return [{"id": str(r["id"]), "periodStart": r["starts_at"],
                     "periodEnd": r["ends_at"], **(r["statement"] or {})}
                    for r in cur.fetchall()]

    # -- the browser session ---------------------------------------------

    @app.post("/v1/console/session", status_code=status.HTTP_201_CREATED)
    def sign_in(body: SessionIn, request: Request, response: Response) -> dict[str, Any]:
        """Exchange an API key for a browser session.

        The cookie is not the key. A key is a long-lived bearer credential
        shown once; putting it in a cookie puts it in browser storage, in
        history, and on every request to this origin forever.
        """
        from runtime.api import session as console_session
        from runtime.crypto import hash_token as _hash

        with db.admin_tx() as cur:
            cur.execute("select * from zolts_internal.resolve_api_key(%s)",
                        (_hash(body.api_key),))
            row = cur.fetchone()
        if row is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")

        opened = console_session.open_session(
            db, str(row["tenant_id"]), str(row["key_id"]),
            ip=(request.client.host if request.client else None))
        secure = request.url.scheme == "https"
        response.set_cookie(
            console_session.COOKIE, opened.token, httponly=True, secure=secure,
            samesite="strict", path="/", max_age=console_session.TTL_HOURS * 3600)
        # Readable by the page on purpose: the double-submit token has to be
        # echoed in a header, and nothing cross-origin can read this origin's
        # cookies to do it.
        response.set_cookie(
            console_session.CSRF_COOKIE, opened.csrf, httponly=False, secure=secure,
            samesite="strict", path="/", max_age=console_session.TTL_HOURS * 3600)
        with db.tenant_tx(str(row["tenant_id"])) as cur:
            ledger.audit(cur, str(row["tenant_id"]), actor=f"key:{row['key_id']}",
                         action="console.signed_in", subject=opened.session_id, detail={})
        return {"expires_at": opened.expires_at.isoformat(),
                "csrf": opened.csrf}

    @app.delete("/v1/console/session", status_code=status.HTTP_204_NO_CONTENT)
    def sign_out(response: Response,
                 principal: Principal = CurrentPrincipal) -> Response:
        from runtime.api import session as console_session

        with db.tenant_tx(principal.tenant_id) as cur:
            cur.execute("update console_session set revoked_at = now()"
                        " where api_key_id = %s and revoked_at is null",
                        (principal.key_id,))
        out = Response(status_code=status.HTTP_204_NO_CONTENT)
        out.delete_cookie(console_session.COOKIE, path="/")
        out.delete_cookie(console_session.CSRF_COOKIE, path="/")
        return out

    # -- onboarding ------------------------------------------------------

    @app.get("/v1/signup/{token}")
    def describe_invitation(token: str, request: Request) -> dict[str, Any]:
        """What the signup form shows before anything is created."""
        from runtime import onboarding

        _throttle(request)
        try:
            return onboarding.describe(db, token)
        except onboarding.InvitationError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.post("/v1/signup", status_code=status.HTTP_201_CREATED)
    def redeem_invitation(body: SignupIn, request: Request) -> dict[str, Any]:
        """Turn an invitation into a tenant, a first key and its programs.

        The only unauthenticated write path in this runtime. An invalid token,
        an expired one and an already-redeemed one all answer identically:
        telling them apart tells a caller which tokens exist.
        """
        from runtime import onboarding

        _throttle(request)
        try:
            return onboarding.redeem(db, body.token, name=body.name,
                                     blueprint_id=body.blueprint_id)
        except onboarding.InvitationError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # -- entities --------------------------------------------------------

    @app.post("/v1/accounts", status_code=status.HTTP_201_CREATED)
    def upsert_account(body: AccountIn, principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        principal.require("write")
        with db.tenant_tx(principal.tenant_id) as cur:
            row = entities.upsert_account(cur, principal.tenant_id, **body.model_dump())
        return {"id": str(row["id"]), "name": row["name"], "domain": row["domain"]}

    @app.post("/v1/people", status_code=status.HTTP_201_CREATED)
    def upsert_person(body: PersonIn, principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        principal.require("write")
        fields = body.model_dump()
        account_id = fields.pop("account_id", None)
        buying_role = fields.pop("buying_role", None)
        with db.tenant_tx(principal.tenant_id) as cur:
            row = entities.upsert_person(cur, principal.tenant_id, **fields)
            if account_id:
                entities.link(cur, principal.tenant_id, str(row["id"]), account_id,
                              buying_role=buying_role)
        return {"id": str(row["id"]), "email": row["email"]}

    # -- programs --------------------------------------------------------

    @app.post("/v1/programs", response_model=ProgramOut, status_code=status.HTTP_201_CREATED)
    def publish_program(body: ProgramIn, principal: Principal = CurrentPrincipal) -> ProgramOut:
        """Publish a version. Activation is a separate, explicit act.

        A spec that would fail CI must not become live through the API, so this
        runs the same schema validation and the same linter as
        `scripts/validate.py`. Schema failures are rejected; lint findings are
        returned but do not block, which matches how they behave offline.
        """
        principal.require("write")
        metadata = {"key": body.key, "version": body.version, "name": body.name}
        for field, value in (("blueprint", body.blueprint), ("owner", body.owner),
                             ("description", body.description)):
            if value:
                metadata[field] = value
        document = {"apiVersion": "zolts/v1", "kind": "Program",
                    "metadata": metadata, "spec": body.spec}
        try:
            jsonschema.Draft202012Validator(dsl.load_schema()).validate(document)
        except jsonschema.ValidationError as exc:
            raise HTTPException(422,
                                f"{'.'.join(str(p) for p in exc.absolute_path)}: {exc.message}"
                                ) from exc

        program = dsl.Program(key=body.key, version=body.version, spec=body.spec, raw=document)
        findings = dsl.lint(program)
        try:
            enroll.holdout_pct(body.spec, body.key)
        except enroll.HoldoutMissing as exc:
            raise HTTPException(422, str(exc)) from exc

        with db.tenant_tx(principal.tenant_id) as cur:
            row = programs.publish(cur, principal.tenant_id, key=body.key, version=body.version,
                                   spec=body.spec, spec_hash=program.spec_hash,
                                   status="draft", created_by=principal.key_id,
                                   metadata=metadata)
            if body.activate:
                row = programs.activate(cur, str(row["id"]))
            ledger.audit(cur, principal.tenant_id, actor=f"key:{principal.key_id}",
                         action="program.published", subject=str(row["id"]),
                         detail={"key": body.key, "version": body.version,
                                 "activated": body.activate})
        return ProgramOut(id=str(row["id"]), key=row["key"], version=row["version"],
                          status=row["status"], spec_hash=row["spec_hash"], lint=findings)

    @app.post("/v1/programs/{program_id}/activate", response_model=ProgramOut)
    def activate_program(program_id: str, principal: Principal = CurrentPrincipal) -> ProgramOut:
        principal.require("write")
        with db.tenant_tx(principal.tenant_id) as cur:
            try:
                row = programs.activate(cur, program_id)
            except LookupError as exc:
                raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
            ledger.audit(cur, principal.tenant_id, actor=f"key:{principal.key_id}",
                         action="program.activated", subject=program_id, detail={})
        return ProgramOut(id=str(row["id"]), key=row["key"], version=row["version"],
                          status=row["status"], spec_hash=row["spec_hash"])

    @app.get("/v1/programs")
    def list_programs(principal: Principal = CurrentPrincipal) -> list[dict[str, Any]]:
        with db.tenant_tx(principal.tenant_id) as cur:
            cur.execute("select id, key, version, status, spec_hash, created_at from program"
                        " order by key, version")
            return [{**r, "id": str(r["id"])} for r in cur.fetchall()]

    # -- ingest ----------------------------------------------------------

    @app.post("/v1/signals", response_model=IngestOut)
    def ingest_signal(body: SignalIn, principal: Principal = CurrentPrincipal) -> IngestOut:
        principal.require("ingest")
        warnings: list[str] = []
        if not body.dedupe_key:
            warnings.append("no dedupe_key: a replay of this signal will be counted twice")
        with db.tenant_tx(principal.tenant_id) as cur:
            try:
                result = enroll.ingest(cur, principal.tenant_id, **body.model_dump())
            except enroll.HoldoutMissing as exc:
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return IngestOut(
            accepted=True, deduplicated=result.deduplicated, warnings=warnings,
            enrollments=[EnrollmentOut(enrollment_id=r.enrollment_id, program_key=r.program_key,
                                       variant=r.variant, tier=r.tier, score=r.score,
                                       reason=r.reason)
                         for r in result.enrollments])

    # -- reads -----------------------------------------------------------

    @app.get("/v1/enrollments")
    def list_enrollments(program_id: str | None = Query(default=None),
                         limit: int = Query(default=100, le=500),
                         principal: Principal = CurrentPrincipal) -> list[dict[str, Any]]:
        with db.tenant_tx(principal.tenant_id) as cur:
            rows = enrollments.listing(cur, program_id, limit)
        return [{**r, "id": str(r["id"]), "program_id": str(r["program_id"]),
                 "entity_id": str(r["entity_id"])} for r in rows]

    @app.get("/v1/decisions")
    def list_decisions(limit: int = Query(default=100, le=500),
                       principal: Principal = CurrentPrincipal) -> list[dict[str, Any]]:
        """Every allow and every deny, with the rule that produced it."""
        with db.tenant_tx(principal.tenant_id) as cur:
            rows = ledger.decisions(cur, limit)
        return [{**r, "id": str(r["id"]), "subject_id": str(r["subject_id"])} for r in rows]

    @app.get("/v1/actions")
    def list_actions(state: str = Query(default="pending"),
                     limit: int = Query(default=100, le=500),
                     principal: Principal = CurrentPrincipal) -> list[dict[str, Any]]:
        with db.tenant_tx(principal.tenant_id) as cur:
            rows = actions.by_state(cur, state, limit)
        return [{**r, "id": str(r["id"])} for r in rows]

    @app.get("/v1/programs/{program_id}/measurement", response_model=MeasurementOut)
    def measurement(program_id: str, principal: Principal = CurrentPrincipal) -> MeasurementOut:
        """Lift against the holdout, with the effect the sample can actually detect.

        The significance flag is the point of the endpoint. A lift reported
        without its minimum detectable effect is a number that reads as a
        result and is not one.
        """
        with db.tenant_tx(principal.tenant_id) as cur:
            program = programs.get(cur, program_id)
            if program is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "program not found")
            counts = enrollments.variant_counts(cur, program_id)
            cur.execute(
                "select e.variant, count(distinct o.enrollment_id) as converted"
                " from enrollment e join outcome o on o.enrollment_id = e.id"
                " where e.program_id = %s and o.type = any(%s) group by e.variant",
                (program_id, ["opp_created", "meeting", "reply_positive"]))
            converted = {r["variant"]: int(r["converted"]) for r in cur.fetchall()}

        treatment = counts.get("treatment", 0)
        control = counts.get("control", 0)
        t_rate = (converted.get("treatment", 0) / treatment) if treatment else 0.0
        c_rate = (converted.get("control", 0) / control) if control else 0.0
        holdout = float((program["spec"].get("experiment") or {}).get("holdout_pct", 0))
        # A control arm with too few observed conversions does not establish a
        # baseline, and an MDE computed from a floor is a number that looks
        # precise and is not. The rule lives in zolts.experiment so the console
        # and this endpoint cannot disagree about what may be declared.
        resolvable = experiment.is_resolvable(converted.get("treatment", 0),
                                              converted.get("control", 0))
        mde = (experiment.minimum_detectable_effect(baseline_rate=max(c_rate, 0.01),
                                                    n_treatment=treatment, n_control=control)
               if resolvable else float("inf"))
        lift_pp = (t_rate - c_rate) * 100
        return MeasurementOut(
            program_key=program["key"], treatment=treatment, control=control,
            holdout_pct=holdout, treatment_rate=t_rate, control_rate=c_rate,
            lift_pp=lift_pp, minimum_detectable_effect_pp=mde * 100 if resolvable else -1,
            significant=bool(resolvable and lift_pp > mde * 100))

    # -- CRM mappings ----------------------------------------------------

    @app.post("/v1/crm/mappings", status_code=status.HTTP_201_CREATED)
    def publish_mapping(document: dict[str, Any],
                        principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """Connect a CRM this runtime has never seen.

        The mapping is the integration. It is validated at the door for the
        same reason a program is: a document that decides who gets contacted,
        discovered wrong later, is discovered in a sent message.
        """
        principal.require("write")
        from runtime.connectors.crm import sources as crm_sources
        from zolts import mapping as crm_mapping

        try:
            crm_mapping.validate(document)
        except crm_mapping.MappingError as exc:
            raise HTTPException(422, str(exc)) from exc

        provider = document["metadata"]["provider"]
        if provider in crm_sources():
            # A built-in source wins the name everywhere it is resolved, so a
            # mapping claiming it would store cleanly and then never be used.
            raise HTTPException(409, f"'{provider}' is a connector this runtime ships; "
                                     "a mapping cannot claim its name — choose another")

        reads_opt_out = bool((document["spec"]["contacts"].get("consent")))
        with db.tenant_tx(principal.tenant_id) as cur:
            row = mappings.publish(cur, principal.tenant_id, document,
                                   reads_opt_out=reads_opt_out,
                                   created_by=principal.key_id)
            ledger.audit(cur, principal.tenant_id, actor=f"key:{principal.key_id}",
                         action="crm_mapping.published", subject=str(row["id"]),
                         detail={"provider": row["provider"],
                                 "reads_opt_out": reads_opt_out})
        return {"id": str(row["id"]), "provider": row["provider"],
                "spec_hash": row["spec_hash"], "reads_opt_out": reads_opt_out,
                "warning": None if reads_opt_out else
                "this mapping declares no consent field, so every contact it "
                "imports is stored with consent unknown and cannot be contacted "
                "until a basis is established elsewhere"}

    @app.get("/v1/crm/mappings")
    def list_mappings(principal: Principal = CurrentPrincipal) -> list[dict[str, Any]]:
        with db.tenant_tx(principal.tenant_id) as cur:
            rows = mappings.listing(cur)
        return [{"id": str(r["id"]), "provider": r["provider"], "name": r["name"],
                 "reads_opt_out": r["reads_opt_out"], "spec_hash": r["spec_hash"],
                 "active": r["active"]} for r in rows]

    @app.post("/v1/crm/{provider}/records")
    def push_records(provider: str, batch: dict[str, Any],
                     principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """Records posted by a CRM this runtime cannot reach.

        Behind a firewall, or with no API at all. Same mapping, same
        normaliser, same contract as a pulled source — the transport is the
        only thing that differs, and it is the least interesting part.
        """
        principal.require("write")
        from runtime.connectors.generic import GenericSource
        from runtime.connectors.sync import pull_staged

        with db.tenant_tx(principal.tenant_id) as cur:
            stored = mappings.get(cur, provider)
        if stored is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                f"no mapping for '{provider}'; publish one first")
        if stored["document"]["spec"]["transport"]["kind"] != "push":
            # The mapping says this runtime pulls. Accepting a push anyway
            # would leave the document describing one system and the runtime
            # running another.
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"the '{provider}' mapping declares transport 'http', "
                                "so this runtime pulls; republish it with transport "
                                "'push' to post batches")

        source = GenericSource(stored["document"])
        source.stage("accounts", list(batch.get("accounts") or []))
        source.stage("contacts", list(batch.get("contacts") or []))
        report = pull_staged(db, principal.tenant_id, source)
        return {"provider": provider, **{k: v for k, v in report.__dict__.items()}}

    # -- the review queue ------------------------------------------------

    @app.get("/v1/proposals")
    def list_proposals(state: str | None = Query(default=None),
                       limit: int = Query(default=100, le=500),
                       principal: Principal = CurrentPrincipal) -> list[dict[str, Any]]:
        """What an agent drafted, and what each gate said about it."""
        with db.tenant_tx(principal.tenant_id) as cur:
            if state:
                cur.execute("select * from proposal where state = %s"
                            " order by created_at desc limit %s", (state, limit))
                rows = [dict(r) for r in cur.fetchall()]
            else:
                rows = proposals.queue(cur, limit)
        return [{**r, "id": str(r["id"])} for r in rows]

    @app.post("/v1/proposals/{proposal_id}/approve")
    def approve_proposal(proposal_id: str,
                         principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """A human approving a draft the gate would not send unattended.

        This is the only other path from generated text to a provider, and it
        records who took it — which is the question an audit asks.
        """
        principal.require("approve")
        from runtime.engine import generate

        with db.tenant_tx(principal.tenant_id) as cur:
            proposal = proposals.get(cur, proposal_id)
            if proposal is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "proposal not found")
            if proposal["state"] not in proposals.DECIDABLE:
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    f"proposal is already {proposal['state']}")
            action_id = generate.promote(cur, principal.tenant_id, proposal,
                                         approved_by=f"key:{principal.key_id}")
        return {"id": proposal_id, "state": "dispatched", "action_id": action_id}

    @app.post("/v1/proposals/{proposal_id}/reject")
    def reject_proposal(proposal_id: str,
                        principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        principal.require("approve")
        with db.tenant_tx(principal.tenant_id) as cur:
            row = proposals.decide(cur, proposal_id, state="rejected",
                                   approved_by=f"key:{principal.key_id}")
            if row is None:
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    "proposal not found or already decided")
            ledger.audit(cur, principal.tenant_id, actor=f"key:{principal.key_id}",
                         action="proposal.rejected", subject=proposal_id, detail={})
        return {"id": proposal_id, "state": "rejected"}

    # -- the console -----------------------------------------------------

    @app.get("/console", response_class=Response)
    async def serve_console(request: Request) -> Response:
        """The operator surface, with this tenant's live data inlined.

        Same origin as the API, so there is no CORS to configure and no second
        origin in `connect-src`. The data is injected exactly as the static
        build injects the fixture, which means one rendering path rather than
        two and a policy derived from the bytes actually served.
        """
        try:
            principal = await auth.principal(request,
                                             request.headers.get("authorization"),
                                             request.headers.get("x-api-key"))
        except HTTPException:
            # A browser typing the URL sends no key and cannot. Answering 401
            # made the operator surface unopenable by an operator, which is how
            # it shipped with no button on it.
            return Response(content=sign_in_page(), media_type="text/html; charset=utf-8",
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            headers={"cache-control": "no-store",
                                     "content-security-policy": SIGN_IN_CSP,
                                     "x-content-type-options": "nosniff"})

        with db.tenant_tx(principal.tenant_id) as cur:
            cur.execute("select * from tenant where id = %s", (principal.tenant_id,))
            tenant = cur.fetchone()
            data = console.build(cur, tenant)

        if not SURFACE.is_file():
            # The quickstart hands this URL to a first-time operator. A stack
            # trace there says nothing; the name of the missing file says
            # everything.
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"the console surface is missing from this deployment "
                f"({SURFACE}); the API and the worker are unaffected, and "
                f"GET /v1/console returns the same data as JSON")
        rendered = document(inject(SURFACE.read_text(encoding="utf-8"), data),
                            title=f"Zolts — {tenant['name']}")
        return Response(
            content=rendered, media_type="text/html; charset=utf-8",
            headers={
                "Content-Security-Policy": content_security_policy(rendered,
                                                                   connect_src="'self'"),
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "strict-origin-when-cross-origin",
                # A tenant's live figures are not cacheable by anything sitting
                # in front of the API.
                "Cache-Control": "no-store, private",
            })

    @app.get("/v1/console")
    def console_data(principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """The same view model as JSON, for anything that renders it itself."""
        with db.tenant_tx(principal.tenant_id) as cur:
            cur.execute("select * from tenant where id = %s", (principal.tenant_id,))
            return console.build(cur, cur.fetchone())

    return app
