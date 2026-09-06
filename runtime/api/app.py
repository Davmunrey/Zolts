"""The API.

Every route resolves a tenant from the API key and does all its work inside a
tenant-scoped transaction. There is no route that takes a tenant id as a
parameter: the only way to name a tenant is to hold one of its keys.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import jsonschema
from fastapi import FastAPI, HTTPException, Query, Response, status

from runtime.api import console
from runtime.api.auth import CurrentPrincipal, Principal
from runtime.api.schemas import (AccountIn, EnrollmentOut, HealthOut, IngestOut,
                                 MeasurementOut, PersonIn, ProgramIn, ProgramOut, SignalIn)
from runtime.connectors import install_default_connectors, providers_for
from runtime.db import Database
from runtime.engine import enroll
from runtime.repo import actions, enrollments, entities, ledger, programs
from runtime.surface import content_security_policy, document, inject
from zolts import dsl, experiment

SURFACE = Path(__file__).resolve().parent.parent.parent / "design" / "console.html"


def create_app(db: Database, *, install_connectors: bool = True) -> FastAPI:
    app = FastAPI(title="Zolts", version="0.1.0",
                  description="The GTM runtime: signal in, gated action out.")
    app.state.db = db
    if install_connectors:
        install_default_connectors()

    # -- health ----------------------------------------------------------

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

    # -- the console -----------------------------------------------------

    @app.get("/console", response_class=Response)
    def serve_console(principal: Principal = CurrentPrincipal) -> Response:
        """The operator surface, with this tenant's live data inlined.

        Same origin as the API, so there is no CORS to configure and no second
        origin in `connect-src`. The data is injected exactly as the static
        build injects the fixture, which means one rendering path rather than
        two and a policy derived from the bytes actually served.
        """
        with db.tenant_tx(principal.tenant_id) as cur:
            cur.execute("select * from tenant where id = %s", (principal.tenant_id,))
            tenant = cur.fetchone()
            data = console.build(cur, tenant)

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
