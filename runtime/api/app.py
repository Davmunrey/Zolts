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
from fastapi.responses import JSONResponse, PlainTextResponse

from runtime.api import auth, console, webhooks
from runtime.api.signin import SIGN_IN_CSP, sign_in_page
from runtime.api.auth import CurrentPrincipal, Principal
from runtime.api.schemas import (AccountIn, BaselineIn, BatchIn, EnrichIn, EnrollmentOut,
                                 HealthOut, IngestOut, KeyIn, MeasurementOut,
                                 PersonIn, ProgramIn, ProgramOut, ResearchIn,
                                 SendingActIn, SessionIn, SignalIn, SignupIn)
from runtime.api.throttle import Throttle, caller_of
from runtime.connectors import install_default_connectors, providers_for
from runtime.db import Database
from runtime.crypto import Keyring  # noqa: F401 - names the threaded key's type
from runtime.engine import admission, enroll
from runtime import (batches, outbox, preview, replyrates, reportsig, sendingcontrol,
                     signalfunnel, timeline)
from runtime.repo import (actions, baseline as baseline_repo, enrollments, entities,
                          ledger, mappings, programs, proposals, reports as reports_repo,
                          tasks as tasks_repo)
from runtime.surface import build_id, content_security_policy, document, inject
from zolts import dsl, experiment
from zolts import metrics

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
               secret_key: "str | Keyring | None" = None) -> FastAPI:
    app = FastAPI(title="Zolts", version="0.1.0",
                  description="The GTM runtime: signal in, gated action out.")
    app.state.db = db
    # A ring when the caller has one, so a credential sealed under a key being
    # rotated out still opens; a bare key when the app is built from the
    # environment alone.
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
    # The same pair the receiver uses. One definition of "the agent
    # layer is configured", so a deployment cannot have a researcher
    # and no triage, or the reverse.
    app.state.agent_factory = app.state.triage_factory

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
    def liveness(response: Response) -> dict[str, Any]:
        """Whether this deployment is doing its job, not whether it is up.

        Deliberately not tenant-scoped and deliberately counts only: it answers
        an operator's question, and returning a tenant's identifiers on an
        unauthenticated path would answer a different one.

        **A failing signal answers 503.** `docs/20` says "point a monitor at
        it", and what a monitor reads is the status code: this endpoint used to
        answer 200 with `draining: false` in the body, so an uptime check saw a
        healthy deployment while the outbox was stalled. `smoke_deployed.py`
        was already written for the 503 that nothing sent.

        Fly's own health check probes `/health`, not this path, so a stalled
        outbox does not make Fly restart the API — which would be the wrong
        response to a worker's problem, and would make it worse.
        """
        from runtime import liveness as liveness_module

        try:
            report = liveness_module.check(db)
        except Exception as exc:  # noqa: BLE001 - a monitor must get an answer
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"draining": False, "signals": [
                {"name": "database", "ok": False, "detail": str(exc), "value": 0}]}
        body = report.as_dict()
        if not body["draining"]:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return body

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

        # What it costs so far, not only what it consumed. A tenant who can
        # see credits and not euros finds out what a period cost when the
        # invoice arrives, which is the surprise `docs/18` I6 exists to avoid.
        from decimal import Decimal
        from zolts.billing import price_credits

        overage_eur = price_credits(budget.overage)
        return {"plan": tenant["plan"],
                "periodStart": period["starts_at"], "periodEnd": period["ends_at"],
                "includedCredits": float(period["included_credits"]),
                "consumedCredits": float(budget.consumed),
                "remainingCredits": float(budget.remaining),
                "creditCeiling": float(budget.ceiling),
                "shareUsed": float(budget.share_used),
                "alerting": budget.alerting,
                "platformEur": float(period["platform_eur"]),
                "overageCredits": float(budget.overage),
                "overageEur": float(overage_eur),
                "projectedTotalEur": float(Decimal(str(period["platform_eur"])) + overage_eur),
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

    # -- the frozen reports ----------------------------------------------

    @app.get("/v1/programs/{program_id}/reports")
    def program_reports(program_id: str,
                        principal: Principal = CurrentPrincipal) -> list[dict[str, Any]]:
        """Every incrementality report frozen for a program, newest period first.

        Read-only. A report is frozen by the period close (ADR-043), never on
        request: a report frozen when somebody asks for one is a report
        frozen on a favourable read, which `docs/10` lists as an anti-pattern.
        """
        with db.tenant_tx(principal.tenant_id) as cur:
            if programs.get(cur, program_id) is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "program not found")
            return [reports_repo.as_dict(r) for r in reports_repo.for_program(cur, program_id)]

    @app.get("/v1/reports/{report_id}")
    def read_report(report_id: str,
                    format: str = Query("json", pattern="^(json|markdown)$"),
                    principal: Principal = CurrentPrincipal) -> Any:
        """One frozen report: its canonical fields and digest, or the document
        itself as it was stored — never re-rendered."""
        with db.tenant_tx(principal.tenant_id) as cur:
            row = reports_repo.get(cur, report_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
        if format == "markdown":
            return PlainTextResponse(row["rendered"], media_type="text/markdown")
        return reports_repo.as_dict(row)

    # -- bulk, with reasons ----------------------------------------------

    _BATCH_REFUSALS = {
        "unknown_act": "that is not an act this queue offers",
        "no_rows": "no rows were named",
        "too_many": f"a batch is at most {batches.MAX_BATCH} rows",
        "no_reason": "a reason is required: forty approvals with no sentence is forty "
                     "clicks, not an act",
        "reason_too_short": "the reason is too short to be a reason",
        "reason_says_nothing": "the reason restates the act instead of explaining it",
    }

    def _batch(kind: str, body: BatchIn, principal: Principal) -> dict[str, Any]:
        """One act over many rows, one written reason, each row under its own
        savepoint: a stale row is a named refusal and the rest proceed
        (docs/28, OX-7). The `approve` scope, because every act here is a
        person taking responsibility for something the runtime cannot verify.
        """
        principal.require("approve")
        with db.tenant_tx(principal.tenant_id) as cur:
            try:
                outcome = batches.run(cur, principal.tenant_id, kind=kind, act=body.act,
                                      ids=body.ids, reason=body.reason,
                                      actor=f"key:{principal.key_id}")
            except batches.BatchRefused as refused:
                raise HTTPException(
                    422, {"refused": refused.key,
                          "message": _BATCH_REFUSALS.get(refused.key, refused.key)})
        return outcome.as_dict()

    @app.post("/v1/proposals/batch")
    def batch_proposals(body: BatchIn, principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        return _batch("proposals", body, principal)

    @app.post("/v1/tasks/batch")
    def batch_tasks(body: BatchIn, principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        return _batch("tasks", body, principal)

    @app.post("/v1/outbox/batch")
    def batch_outbox(body: BatchIn, principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        return _batch("outbox", body, principal)

    # -- the report a CFO opens ------------------------------------------

    @app.get(reportsig.KEYS_PATH)
    def signing_keys() -> dict[str, Any]:
        """The keys this instance signs exported reports with: the current
        one and any a previous sealing secret still names. Public keys are
        public, so this needs no credential (ADR-068).
        """
        return reportsig.published_keys(app.state.secret_key)

    @app.get("/v1/reports/{report_id}/export")
    def export_report(report_id: str, request: Request,
                      principal: Principal = CurrentPrincipal) -> Any:
        """One frozen report as a signed document: the figures, the digest,
        the prose, and a signature over the digest and the prose made with
        the key published above. Verified anywhere by
        `scripts/verify_report.py` with the public key and nothing else
        (docs/28, OX-5).
        """
        with db.tenant_tx(principal.tenant_id) as cur:
            row = reports_repo.get(cur, report_id)
            if row is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
            program = programs.get(cur, str(row["program_id"]))
        keys_url = str(request.base_url).rstrip("/") + reportsig.KEYS_PATH
        doc = reportsig.export(dict(row), program, app.state.secret_key, keys_url=keys_url)
        name = f"{program['key']}-{row['period_end'].isoformat()}-incrementality-report.json"
        return JSONResponse(doc, headers={"Content-Disposition": f'attachment; filename="{name}"'})

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

    # -- the baseline ----------------------------------------------------

    @app.get("/v1/baseline")
    def read_baseline(principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """What this tenant's GTM cost and produced before Zolts, frozen."""
        with db.tenant_tx(principal.tenant_id) as cur:
            row = baseline_repo.get(cur)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                "no baseline is frozen for this tenant")
        return baseline_repo.as_dict(row)

    @app.post("/v1/baseline", status_code=status.HTTP_201_CREATED)
    def freeze_baseline(body: BaselineIn,
                        principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """Freeze the baseline. Once: a second capture answers 409 (ADR-042)."""
        from zolts.baseline import BaselineError, from_mapping

        principal.require("write")
        try:
            captured = from_mapping(body.model_dump())
        except BaselineError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        with db.tenant_tx(principal.tenant_id) as cur:
            try:
                row = baseline_repo.freeze(cur, principal.tenant_id, captured,
                                           signed_by=body.signed_by,
                                           captured_by=f"key:{principal.key_id}")
            except baseline_repo.BaselineAlreadyFrozen as exc:
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
            ledger.audit(cur, principal.tenant_id, actor=f"key:{principal.key_id}",
                         action="baseline.frozen", subject=str(principal.tenant_id),
                         detail={"digest": row["digest"], "signed_by": body.signed_by,
                                 "source": row["source"]})
        return baseline_repo.as_dict(row)

    # -- entities --------------------------------------------------------

    @app.get("/v1/accounts")
    def list_accounts(limit: int = Query(200, ge=1, le=1000),
                      principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """The tenant's accounts and contacts, and what is missing on them.

        Both of these were POST-only. A tenant could create a prospect and had
        no way to read one back, which made the console's prospect list
        impossible and left enrichment reachable only from the CLI.
        """
        with db.tenant_tx(principal.tenant_id) as cur:
            return console.prospects_view(cur, limit=limit)

    @app.post("/v1/enrich")
    def enrich_entities(body: EnrichIn,
                        principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """Buy a missing field for named entities.

        Named rather than "everything missing": this spends real money, and an
        endpoint that bills a data budget for whatever the caller happened to
        have unresolved is one nobody can predict the cost of. The console
        sends the rows the operator selected.
        """
        principal.require("write")
        from runtime import enrichment

        # The key the app was created with, not a fresh read of the whole
        # environment. `Settings.from_env()` requires the database URL too, so
        # a handler that calls it fails in any process configured differently
        # from the one that built the app — which is every test, and any
        # deployment that passes configuration in rather than exporting it.
        results = []
        with db.tenant_tx(principal.tenant_id) as cur:
            enrichment.install_declared(cur)
            cur.execute("select * from tenant where id = %s", (principal.tenant_id,))
            tenant = cur.fetchone()
            table = "account" if body.field == "firmographics" else "person"
            for entity_id in body.ids:
                cur.execute(f"select * from {table} where id = %s", (entity_id,))
                row = cur.fetchone()
                if row is None:
                    continue
                account = None
                if table == "person":
                    cur.execute(
                        "select a.* from account a join membership m"
                        " on m.account_id = a.id where m.person_id = %s limit 1",
                        (entity_id,))
                    found = cur.fetchone()
                    account = dict(found) if found else None
                results.append(enrichment.resolve(
                    cur, tenant, field_name=body.field, entity=dict(row),
                    account=account, legal_basis=body.legal_basis,
                    secret_key=app.state.secret_key).as_dict())

        resolved = [r for r in results if r["hit"]]
        return {"field": body.field, "asked": len(results),
                "resolved": len(resolved),
                "creditsBilled": sum(r["credits"] for r in resolved),
                "results": results,
                "note": "misses are paid for and not billed to the tenant"}

    @app.post("/v1/research")
    def research_accounts(body: ResearchIn,
                          principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """Write a research dossier for named accounts.

        The most expensive action in the price list, so it is deliberate on
        both sides: named accounts rather than a sweep, a write scope, and a
        dossier that is still current is served from storage rather than
        rewritten. `force` costs again and says so in the result.
        """
        principal.require("write")
        from runtime import research

        made = app.state.agent_factory
        client, guard, budget = (None, None, {}) if made is None else made(principal.tenant_id)
        results = []
        with db.tenant_tx(principal.tenant_id) as cur:
            cur.execute("select * from tenant where id = %s", (principal.tenant_id,))
            tenant = dict(cur.fetchone())
            for account_id in body.account_ids:
                cur.execute("select * from account where id = %s", (account_id,))
                row = cur.fetchone()
                if row is None:
                    continue
                results.append(research.build(
                    cur, tenant, dict(row), client=client, guard=guard,
                    consumed_usd=budget.get("consumed_usd"),
                    limit_usd=budget.get("limit_usd"), force=body.force).as_dict())

        return {"asked": len(results),
                "written": sum(1 for r in results if not r["reused"] and r["state"] != "refused"),
                "reused": sum(1 for r in results if r["reused"]),
                "creditsBilled": round(sum(r["credits"] for r in results), 4),
                "results": results,
                "note": "a dossier is reused while nothing new has happened on the "
                        "account, and a refusal is never billed"}

    @app.get("/v1/research/{account_id}")
    def read_dossier(account_id: str,
                     principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """The stored dossier for one account, with what was struck out of it.

        The dropped claims are returned rather than hidden. What the model
        wanted to say and could not support is the most useful thing on the
        page for an operator deciding how much of it to believe.
        """
        from runtime import research

        with db.tenant_tx(principal.tenant_id) as cur:
            stored = research.latest(cur, account_id)
            if stored is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND,
                                    "no dossier for that account yet")
            cur.execute("select * from account where id = %s", (account_id,))
            account = cur.fetchone()
            _, newest, _ = research.evidence_for(cur, dict(account)) if account else (None, None, 0)
        seen = stored["built_through"]
        return {
            "account": account_id, "state": stored["state"], "body": stored["body"],
            "evidence": stored["evidence"], "dropped": stored["dropped"],
            "model": stored["model"], "promptVersion": stored["prompt_version"],
            "writtenAt": stored["created_at"].isoformat(),
            "current": newest is None or (seen is not None and seen >= newest),
        }

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
            # No holdout, an audience nothing can evaluate, an unpriced
            # enrichment field, an override that cannot be read as stricter:
            # each is a program that looks armed and does something other than
            # what it says. A 422 here costs a retry; finding out after
            # activation costs a campaign. `publish` runs the same check, so
            # the four paths that do not come through this handler get the
            # same answer — this call exists to refuse without opening a
            # transaction and to say 422 rather than 500.
            #
            # It cannot answer every question and does not pretend to: whether
            # the programme loosens its archetype needs the tenant's blueprint,
            # which needs a cursor (ADR-057). So this is a cheap pre-check and
            # `publish` is the authority — and the authority's refusal is caught
            # below as the same 422. Leaving that one uncaught turned an
            # inadmissible programme into a 500, which is the status code this
            # comment exists to prevent (D-95).
            admission.check(body.spec, body.key)
        except admission.NotAdmissible as exc:
            raise HTTPException(422, str(exc)) from exc

        with db.tenant_tx(principal.tenant_id) as cur:
            try:
                row = programs.publish(cur, principal.tenant_id, key=body.key,
                                       version=body.version, spec=body.spec,
                                       spec_hash=program.spec_hash, status="draft",
                                       created_by=principal.key_id, metadata=metadata)
            except admission.NotAdmissible as exc:
                raise HTTPException(422, str(exc)) from exc
            except programs.VersionIsImmutable as exc:
                # 409, not 422: the document is valid, the version is taken.
                # An operator who republishes v2.1.0 with a new holdout needs
                # to be told to bump it, not that their spec is wrong.
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
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
            # Activation is the moment "before Zolts" ends. A tenant that
            # starts without a frozen baseline has no before to compare
            # against, ever (decision 35). Noted rather than refused, and
            # noted where an operator looks.
            lint: list[str] = []
            if baseline_repo.get(cur) is None:
                lint.append("no baseline is frozen for this tenant; the lift this "
                            "program measures will have nothing before it to compare "
                            "against (POST /v1/baseline, or `zolts baseline`)")
                ledger.audit(cur, principal.tenant_id, actor=f"key:{principal.key_id}",
                             action="program.activated_without_baseline",
                             subject=program_id, detail={"program": row["key"]})
        return ProgramOut(id=str(row["id"]), key=row["key"], version=row["version"],
                          status=row["status"], spec_hash=row["spec_hash"], lint=lint)

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
        # `docs/06` targets ingestion to signal available, and the moment of
        # ingestion is this one — before the transaction opens, not inside it.
        # Taken after, it would measure nothing: the work the stage is about is
        # the trigger evaluation across every live programme, which happens
        # between here and the write (SIG-1).
        received_at = _now()
        with db.tenant_tx(principal.tenant_id) as cur:
            try:
                result = enroll.ingest(cur, principal.tenant_id, received_at=received_at,
                                       **body.model_dump())
            except enroll.HoldoutMissing as exc:
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        # A predicate the payload could not answer is a non-match, not a 500 —
        # and the sender is the only party who can fix it, so they are told in
        # the answer rather than in a log they cannot read. D-37.
        warnings.extend(result.warnings)
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

    @app.get("/v1/audit")
    def list_audit(limit: int = Query(default=100, le=500),
                   principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """Who authorised what, and when.

        Written since the first key was issued and readable only from a SQL
        prompt until now — which makes it evidence nobody can produce during
        the diligence it exists for.
        """
        with db.tenant_tx(principal.tenant_id) as cur:
            return console.audit_view(cur, limit)

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
            # The metric this programme declared, counted inside the window it
            # declared. Every programme used to be measured on the same three
            # outcome types with no window at all (D-51).
            metric = metrics.resolve(
                (program["spec"].get("experiment") or {}).get("primary_metric"))
            converted = console._converted(cur, program_id, metric)

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
            significant=bool(resolvable and lift_pp > mde * 100),
            primary_metric=metric.name, metric_counts=metric.describes,
            metric_window_days=metric.window_days)

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
        # Deals, when the mapping says how to read them. A push mapping that
        # declares an `opportunities` block and never receives any would leave
        # the exclusion unanswerable, which is the same hole as a CRM that
        # cannot read them — except silent, because the mapping claims it can.
        source.stage("opportunities", list(batch.get("opportunities") or []))
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

    @app.get("/v1/tasks")
    def list_tasks(overdue: bool = False,
                   principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """The work waiting for a person, and what is late.

        A step on the `task` or `voice` channel is real work for a human. The
        runtime created it and, until D-83, nothing could ever close it: every
        path that moves a touch off `queued` is driven by a provider event, and
        a task has no provider.
        """
        principal.require("read")
        with db.tenant_tx(principal.tenant_id) as cur:
            rows = (tasks_repo.overdue(cur) if overdue else tasks_repo.open_tasks(cur))
            return {"tasks": [_task(row) for row in rows],
                    "counts": tasks_repo.counts(cur)}

    @app.post("/v1/tasks/{task_id}/complete")
    def complete_task(task_id: str,
                      principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """Say the work is done. The `approve` scope, because it is the same
        kind of act as approving a proposal: a person taking responsibility for
        something the runtime cannot verify."""
        principal.require("approve")
        with db.tenant_tx(principal.tenant_id) as cur:
            row = tasks_repo.complete(cur, task_id, by=f"key:{principal.key_id}")
            if row is None:
                # One 409 for both, on purpose: whether a task id belongs to
                # another tenant, to no task at all, or to one already done is
                # not something an unauthenticated guess should be able to
                # tell apart.
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    "task not found or already completed")
            ledger.audit(cur, principal.tenant_id, actor=f"key:{principal.key_id}",
                         action="task.completed", subject=task_id,
                         detail={"step": row["step_key"],
                                 "late": bool(row["due_at"]
                                              and row["completed_at"] > row["due_at"])})
        return _task(row)

    # -- what activating it would do today ------------------------------

    @app.get("/v1/programs/{program_id}/preview")
    def program_preview(program_id: str,
                        principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """A forecast from the functions that will enrol, writing nothing.

        Activate is the scariest click in the product and the one with no
        preview (docs/28, OX-2). An audience the runtime cannot evaluate is
        reported as that, never as zero.
        """
        principal.require("read")
        with db.tenant_tx(principal.tenant_id) as cur:
            cur.execute("select * from program where id = %s", (program_id,))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "no such program")
            return preview.for_program(cur, principal.tenant_id, dict(row))

    # -- which copy works -------------------------------------------------

    @app.get("/v1/programs/{program_id}/copy")
    def program_copy(program_id: str,
                     principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """Reply and positive-reply rates per step, with the sample beside
        the rate and no rate under the floor the experiment accepts
        (docs/28, OX-4). A withheld rate says why.
        """
        from zolts.replyrates import FLOOR

        principal.require("read")
        with db.tenant_tx(principal.tenant_id) as cur:
            cur.execute("select id, key, spec from program where id = %s", (program_id,))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "no such program")
            return {"kind": "copy", "program": row["key"], "floor": FLOOR,
                    "rows": replyrates.for_program(cur, str(row["id"]), row["spec"] or {})}

    # -- which signals earn their keep -----------------------------------

    @app.get("/v1/signals/funnel")
    def signal_funnel(principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """One row per catalogue signal: fired, listened to, enrolled, held
        out, reached, converted inside the programme's declared window.

        Descriptive, not attributed (docs/28, OX-3). A signal that never fired
        has a row: it is the one being paid for and producing nothing.
        """
        principal.require("read")
        with db.tenant_tx(principal.tenant_id) as cur:
            return signalfunnel.for_tenant(cur)

    # -- why this person -------------------------------------------------

    @app.get("/v1/people/{person_id}/timeline")
    def person_timeline(person_id: str,
                        principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """Everything that touched one contact, newest first.

        Six tables held the story and no screen read one contact across all
        six. A DPO answering a subject access request and a CRO asking why an
        email went are asking the same question, and this is the one answer
        (docs/28, OX-1; the first half of docs/11 COMP-2).
        """
        principal.require("read")
        with db.tenant_tx(principal.tenant_id) as cur:
            found = timeline.for_person(cur, person_id)
        if found is None:
            # Another tenant's person and no person at all read the same:
            # a difference here is a way to enumerate somebody else's book.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such person")
        return found

    @app.get("/v1/people/{person_id}/subject-request")
    def person_subject_request(person_id: str,
                               principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """The access and portability half of a subject access request.

        Built on the timeline so the DPO's export and the operator's screen
        cannot disagree. Erasure is the other half and is still not built
        (docs/11, COMP-2).
        """
        principal.require("read")
        with db.tenant_tx(principal.tenant_id) as cur:
            found = timeline.subject_request(cur, person_id)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such person")
        return found

    # -- the outbox ------------------------------------------------------

    # `docs/25` told the operator to requeue a dead action after re-entering an
    # expired credential, and there was no requeue anywhere. A revive re-runs
    # with the action's own idempotency key, which is the same guarantee an
    # expired lease already relies on, so it cannot double-send.

    _OUTBOX_REFUSALS = {
        "no_reason": "a reason is required: the row that says somebody put a "
                     "failed action back on the wire, and not why, is the row "
                     "somebody reads back with only it",
        "reason_too_short": "the reason is too short to be a reason",
        "reason_says_nothing": "the reason restates the action instead of "
                               "explaining it",
        "not_dead": "no action by that id is dead in this tenant",
    }

    def _outbox_act(action_id: str, body: SendingActIn, principal: Principal,
                    act: str) -> dict[str, Any]:
        principal.require("approve")
        run = outbox.revive if act == "revive" else outbox.discard
        with db.tenant_tx(principal.tenant_id) as cur:
            outcome = run(cur, principal.tenant_id, action_id,
                          reason=body.reason, actor=f"key:{principal.key_id}")
            if not outcome.done:
                key = outcome.refusal.value
                raise HTTPException(
                    status.HTTP_409_CONFLICT if key == "not_dead" else 422,
                    {"refused": key, "message": _OUTBOX_REFUSALS[key]})
            state = outcome.row["state"]
        return {"id": action_id, "state": state, "reason": body.reason.strip()}

    @app.get("/v1/outbox/dead")
    def list_dead(limit: int = Query(default=100, le=500),
                  principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """What gave up, with what killed it."""
        principal.require("read")
        with db.tenant_tx(principal.tenant_id) as cur:
            return outbox.dead(cur, limit)

    @app.post("/v1/outbox/{action_id}/revive")
    def revive_action(action_id: str, body: SendingActIn,
                      principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """Put it back on the wire, with the idempotency key it already had."""
        return _outbox_act(action_id, body, principal, "revive")

    @app.post("/v1/outbox/{action_id}/discard")
    def discard_action(action_id: str, body: SendingActIn,
                       principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """Retire it, so a row nobody will act on stops being an alarm."""
        return _outbox_act(action_id, body, principal, "discard")

    # -- the sending switch ----------------------------------------------

    # `runtime/breakers.py` was the only writer of `sending_domain.paused`, so
    # the only actor that could ever stop a send was a cut-off firing on rates
    # already earned. An operator who knew before the numbers did could watch.
    # These are the two acts, each narrow: one column, nothing else in the same
    # statement. The `approve` scope, because stopping a customer's outbound
    # mail and starting it again are both a person taking responsibility for
    # something the runtime cannot decide.

    _SENDING_REFUSALS = {
        "no_reason": "a reason is required: an audit row that records who "
                     "stopped the sending and not why is the row somebody "
                     "reads back during the next incident",
        "reason_too_short": "the reason is too short to be a reason",
        "reason_says_nothing": "the reason restates the action instead of "
                               "explaining it",
        "not_registered": "no sending domain by that name is registered",
        "already_paused": "that domain is already paused",
        "already_sending": "that domain is not paused",
    }

    def _sending_refusal(outcome: Any) -> HTTPException:
        key = outcome.refusal.value
        # 422 as the literal: starlette renamed the constant and keeping the
        # old name emits a deprecation warning on every refusal, which is noise
        # in the one place an operator's mistake is being reported.
        code = (status.HTTP_409_CONFLICT
                if key in ("not_registered", "already_paused", "already_sending")
                else 422)
        return HTTPException(code, {"refused": key,
                                    "message": _SENDING_REFUSALS[key]})

    @app.post("/v1/sending/domains/{name}/pause")
    def pause_domain(name: str, body: SendingActIn,
                     principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """Stop this domain sending, now."""
        principal.require("approve")
        with db.tenant_tx(principal.tenant_id) as cur:
            outcome = sendingcontrol.pause(
                cur, principal.tenant_id, name, reason=body.reason,
                actor=f"key:{principal.key_id}")
            if not outcome.done:
                raise _sending_refusal(outcome)
        return {"domain": name, "paused": True, "reason": body.reason.strip()}

    @app.post("/v1/sending/domains/{name}/resume")
    def resume_domain(name: str, body: SendingActIn,
                      principal: Principal = CurrentPrincipal) -> dict[str, Any]:
        """Let this domain send again, and say which kind of act that was.

        `resumption` is returned as well as recorded: an operator who has just
        overridden a live cut-off should be told so by the thing they pressed,
        not only by a log they will not open.
        """
        principal.require("approve")
        with db.tenant_tx(principal.tenant_id) as cur:
            outcome = sendingcontrol.resume(
                cur, principal.tenant_id, name, reason=body.reason,
                actor=f"key:{principal.key_id}")
            if not outcome.done:
                raise _sending_refusal(outcome)
        return {"domain": name, "paused": False,
                "resumption": outcome.resumption.value,
                "rule": outcome.verdict.rule_key,
                "rationale": outcome.verdict.rationale}

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
        template = SURFACE.read_text(encoding="utf-8")
        rendered = document(inject(template, data), build=build_id(template),
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
    def console_data(request: Request, principal: Principal = CurrentPrincipal) -> Any:
        """The same view model as JSON, for anything that renders it itself —
        the console included, which re-reads it after every action and every
        thirty seconds while its tab is visible (docs/28, OX-6). An ETag over
        the body lets a quiet console pay one small request for a 304 instead
        of re-rendering a model that has not moved.
        """
        import hashlib
        import json as _json

        from fastapi.encoders import jsonable_encoder

        with db.tenant_tx(principal.tenant_id) as cur:
            cur.execute("select * from tenant where id = %s", (principal.tenant_id,))
            model = console.build(cur, cur.fetchone())
        body = _json.dumps(jsonable_encoder(model), separators=(",", ":")).encode("utf-8")
        tag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": tag})
        return Response(content=body, media_type="application/json", headers={"ETag": tag})

    return app


def _task(row: dict[str, Any]) -> dict[str, Any]:
    """One human task, as an operator surface reads it."""
    due, completed = row.get("due_at"), row.get("completed_at")
    return {
        "id": str(row["id"]),
        "channel": row["channel"],
        "step": row["step_key"],
        "program": row.get("program_key"),
        "createdAt": row["created_at"],
        "dueAt": due,
        "completedAt": completed,
        "completedBy": row.get("completed_by"),
        # Computed here rather than stored: *late* is a comparison, and a
        # stored copy of one is a second answer waiting to disagree with the
        # two timestamps beside it.
        "late": bool(due and (completed or _now()) > due),
    }


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
