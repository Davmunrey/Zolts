"""End-to-end smoke run against a live database.

Publishes a real program from `examples/programs/`, ingests a signal that
triggers it, runs the worker, delivers a signed reply webhook, and reads the
measurement back. It uses the fake connector so nothing leaves the machine;
what is being proved is that the whole loop is connected — signal to gated
action to recorded outcome to a lift the surface may or may not report.

    ZOLTS_DATABASE_URL=... ZOLTS_SECRET_KEY=... python3 scripts/smoke_runtime.py
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runtime.config import Settings                       # noqa: E402
from runtime.connectors import register                   # noqa: E402
from runtime.connectors.fake import FakeConnector         # noqa: E402
from runtime.api import console                            # noqa: E402
from runtime.db import Database                           # noqa: E402
from runtime.engine import enroll                         # noqa: E402
from runtime.engine.worker import Worker                  # noqa: E402
from runtime.engine import inbound                          # noqa: E402
from runtime.provision import (create_tenant, create_webhook_endpoint,  # noqa: E402
                               store_connection)
from runtime.repo import actions, entities, ledger, programs   # noqa: E402
from zolts import dsl                                     # noqa: E402

PROGRAM = Path(__file__).resolve().parent.parent / "examples/programs/01-b2b-saas-sales-led.yaml"


def main() -> int:
    settings = Settings.from_env()
    db = Database(settings.database_url, settings.app_database_url)
    db.migrate()
    db.grant_app_role()
    register(FakeConnector())

    program = dsl.load(PROGRAM)
    findings = dsl.lint(program)
    tenant = create_tenant(db, slug=f"smoke-{uuid.uuid4().hex[:8]}", name="Smoke Co",
                           region="eu", blueprint_id=program.raw["metadata"]["blueprint"])
    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=settings.secret_key)

    with db.tenant_tx(tid) as cur:
        row = programs.publish(cur, tid, key=program.key, version=program.version,
                               spec=program.spec, spec_hash=program.spec_hash, status="draft")
        programs.activate(cur, str(row["id"]))

        account = entities.upsert_account(cur, tid, name="Northwind Analytics",
                                          domain="northwind.example", country="ES",
                                          employee_band="51-200")
        person = entities.upsert_person(
            cur, tid, email="dana@northwind.example", full_name="Dana Cruz", country="ES",
            consent_state={"email": {"basis": "legitimate_interest", "source": "smoke"}})
        entities.link(cur, tid, str(person["id"]), str(account["id"]), buying_role="economic")

        result = enroll.ingest(
            cur, tid, entity_type="account", entity_id=str(account["id"]),
            type="funding.round", strength=0.72, half_life_h=720, source="smoke",
            legal_basis="legitimate_interest",
            payload={"stage": "series_a", "amount_usd": 12_000_000},
            observed_at=datetime.now(timezone.utc), dedupe_key=f"smoke-{uuid.uuid4().hex}")

    worker = Worker(db, secret_key=settings.secret_key)
    tick = worker.tick([tid])

    # Close the loop. A reply arriving from the sending provider is what turns
    # a touch into an outcome, and an outcome into a measurable effect. Without
    # this half, "measured by incrementality" depends on someone remembering to
    # post the result by hand.
    create_webhook_endpoint(db, tid, provider="smartlead", secret_key=settings.secret_key)
    with db.tenant_tx(tid) as cur:
        cur.execute("select idempotency_key from touch where status = 'sent' limit 1")
        row = cur.fetchone()
        webhook_effects: list[str] = []
        if row:
            event = inbound.store(cur, tid, provider="smartlead", signature_ok=True, payload={
                "event_type": "reply", "id": f"smoke-{uuid.uuid4().hex}",
                "lead": {"custom_fields": {"zolts_idempotency_key": row["idempotency_key"]}}})
            webhook_effects = inbound.apply(cur, tid, event).effects

    with db.tenant_tx(tid) as cur:
        cur.execute("select channel, step_key, status, provider from touch order by created_at")
        touches = [dict(r) for r in cur.fetchall()]
        decisions = [{k: r[k] for k in ("action", "decision", "rule_key", "jurisdiction")}
                     for r in ledger.decisions(cur, 10)]
        queued = actions.pending_count(cur)
        cur.execute("select id from program where status = 'live' limit 1")
        # The tenant's own row. A stub with the right labels passed every
        # check until the console started reading the billing period, which is
        # keyed by id — and then the smoke run, not a test, was what noticed.
        cur.execute("select * from tenant where id = %s", (tid,))
        measurement = console.build(cur, dict(cur.fetchone()))

    print(json.dumps({
        "program": {"key": program.key, "version": program.version,
                    "spec_hash": program.spec_hash, "lint": findings},
        "enrollments": [e.__dict__ for e in result.enrollments],
        "tick": {k: v for k, v in tick.__dict__.items() if k != "errors"},
        "worker_errors": tick.errors,
        "touches": touches,
        "policy_decisions": decisions,
        "still_queued": queued,
        "webhook_effects": webhook_effects,
        # One reply is one conversion, which is below the floor at which any
        # effect may be declared. The smoke run asserts that it is withheld:
        # the loop being connected is not the same as the loop having a result.
        "measurement": [{k: p[k] for k in ("key", "enrolled", "significant",
                                           "pipeline", "unresolvedReason")}
                        for p in measurement["programs"]],
    }, indent=2, default=str))
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
