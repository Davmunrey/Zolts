"""End-to-end smoke run against a live database.

Publishes a real program from `examples/programs/`, ingests a signal that
should trigger it, runs the worker, and prints what the runtime decided. It
uses the fake connector so nothing leaves the machine; what is being proved is
that the path from signal to gated, recorded action is connected.

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
from runtime.db import Database                           # noqa: E402
from runtime.engine import enroll                         # noqa: E402
from runtime.engine.worker import Worker                  # noqa: E402
from runtime.provision import create_tenant, store_connection  # noqa: E402
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

    with db.tenant_tx(tid) as cur:
        cur.execute("select channel, step_key, status, provider from touch order by created_at")
        touches = [dict(r) for r in cur.fetchall()]
        decisions = [{k: r[k] for k in ("action", "decision", "rule_key", "jurisdiction")}
                     for r in ledger.decisions(cur, 10)]
        queued = actions.pending_count(cur)

    print(json.dumps({
        "program": {"key": program.key, "version": program.version,
                    "spec_hash": program.spec_hash, "lint": findings},
        "enrollments": [e.__dict__ for e in result.enrollments],
        "tick": {k: v for k, v in tick.__dict__.items() if k != "errors"},
        "worker_errors": tick.errors,
        "touches": touches,
        "policy_decisions": decisions,
        "still_queued": queued,
    }, indent=2, default=str))
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
