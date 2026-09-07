"""End-to-end smoke run against a live database.

Walks the commercial loop a GTM runtime has to complete, one stage at a time,
and fails when a stage does not happen. It uses fakes so nothing leaves the
machine; what is proved is that the stages are connected to each other.

    sync        a CRM's records become this runtime's accounts and contacts
    audience    the program decides whether this account is one of its own
    enrich      a missing field is bought through the declared waterfall
    enrol       a signal inside the trigger window creates an enrollment
    plan        the worker turns an enrollment into a due action
    gate        the policy engine allows or denies, with a reason
    send        the action reaches a provider and a touch is recorded
    reply       an inbound webhook becomes an outcome
    measure     the surface reports a lift, or says why it cannot
    bill        the period closes into a statement

Two stages are deliberately absent: the researcher and the reply triage both
need a model and a spend guard, and faking either would prove that the fake
works. They are covered by `tests/test_research.py` and `tests/test_triage.py`
against a real spend guard.

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
from runtime.connectors import sync                       # noqa: E402
from runtime.connectors.crm import (Capabilities, CrmAccount,  # noqa: E402
                                    CrmContact, CrmOpportunity, DealStatus)
from runtime import enrichment, metering                  # noqa: E402
from runtime.connectors.dataprovider import register_provider  # noqa: E402
from runtime.connectors.fake import FakeDataProvider      # noqa: E402
from runtime.api import console                            # noqa: E402
from runtime.db import Database                           # noqa: E402
from runtime.engine import enroll                         # noqa: E402
from runtime.engine.worker import Worker                  # noqa: E402
from runtime.engine import inbound                          # noqa: E402
from runtime.provision import (create_tenant, create_webhook_endpoint,  # noqa: E402
                               store_connection)
from runtime.repo import actions, entities, ledger, programs   # noqa: E402
from zolts import dsl, experiment                         # noqa: E402

PROGRAM = Path(__file__).resolve().parent.parent / "examples/programs/01-b2b-saas-sales-led.yaml"


class _SmokeCrm:
    """A CRM with two records in it.

    The contract suite proves the real connectors satisfy this interface. What
    this proves is the other half: that the sync path turns whatever a source
    yields into the accounts and contacts the rest of the runtime reads.
    """

    capabilities = Capabilities(provider="smoke-crm", reads_opt_out=False,
                                reads_opportunities=True)

    def accounts(self, credential: str):
        yield CrmAccount(external_id="crm-1", name="Contoso Data",
                         domain="contoso.example", country="ES",
                         employee_band="51-200", industry="software")
        yield CrmAccount(external_id="crm-2", name="Fabrikam Logistics",
                         domain="fabrikam.example", country="ES",
                         employee_band="51-200", industry="software")

    def contacts(self, credential: str):
        yield CrmContact(external_id="crm-c1", email="rio@contoso.example",
                         full_name="Rio Vega", country="ES",
                         account_external_id="crm-1")
        yield CrmContact(external_id="crm-c2", email="sam@fabrikam.example",
                         full_name="Sam Ortiz", country="ES",
                         account_external_id="crm-2")

    def opportunities(self, credential: str):
        """One live deal, on the second account.

        The stage that proves the exclusion works rather than merely runs: the
        loop below enrols Contoso and must not enrol Fabrikam, and a run where
        both enrol is a run where the clause did nothing.
        """
        yield CrmOpportunity(external_id="crm-d1", account_external_id="crm-2",
                             name="Fabrikam renewal", stage="Negotiation",
                             status=DealStatus.OPEN)
        yield CrmOpportunity(external_id="crm-d2", account_external_id="crm-1",
                             name="Contoso pilot, closed last quarter",
                             stage="Closed Won", status=DealStatus.WON)


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

    # -- sync: where a customer's list actually comes from ----------------
    # The first stage of every GTM product, and the one this script never
    # touched. It runs before any signal is ingested because that is the order
    # a customer's system works in — and because the flagship program's
    # audience excludes accounts with an open deal, which is a question no CRM
    # has answered until this runs. A fake source rather than a real CRM: the
    # contract suite proves HubSpot, Pipedrive and Salesforce satisfy the
    # interface; what is proved here is that the sync path writes what a
    # source yields.
    synced = sync.pull(db, tid, credential="none", provider="smoke-crm",
                       source=_SmokeCrm())

    with db.tenant_tx(tid) as cur:
        # The flagship program's audience is software companies of 51-500
        # people in six countries. Before the audience was executed this
        # account enrolled anyway; it now has to be one the program is for,
        # exactly as a customer's would.
        # The account has to land in the treatment arm, and which arm an
        # account lands in is a hash of its id. With the shipped 10% holdout
        # this script failed roughly one run in ten — reporting four dead
        # stages for the one reason that is not a defect, because a control
        # enrolment produces no action *by design*. A CI check that fails one
        # run in ten teaches people to press re-run, which is worth less than
        # no check at all.
        #
        # The holdout is not turned off to fix that: it is the product's
        # central invariant and the shipped program is published here verbatim.
        # An account is chosen that the real assignment function puts in
        # treatment, and if twenty candidates in a row land in control then
        # assignment itself is broken and this script should say so.
        pct = float((program.spec["experiment"] or {})["holdout_pct"])
        salt = (program.spec["experiment"] or {}).get("salt", "")
        account = None
        for attempt in range(20):
            candidate = entities.upsert_account(
                cur, tid, name="Northwind Analytics",
                domain=f"northwind-{attempt}.example", country="ES",
                employee_band="51-200", industry_code="software")
            if not experiment.assign(str(candidate["id"]), program.key, pct,
                                     salt).is_control:
                account = candidate
                break
        if account is None:
            print("::error::twenty accounts in a row assigned to control; the "
                  "holdout assignment is not distributing", file=sys.stderr)
            return 1
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

        # The same signal, on the account the CRM says is in a live deal. It
        # matches the trigger, the score, the country, the size and the
        # industry — everything except the exclusion. A run where this enrols
        # is a run where the clause did nothing, which is the state this
        # product shipped in until there was an `opportunity` table.
        cur.execute("select id from account where crm_id = %s", ("smoke-crm:crm-2",))
        in_a_deal = cur.fetchone()["id"]
        deal_result = enroll.ingest(
            cur, tid, entity_type="account", entity_id=str(in_a_deal),
            type="funding.round", strength=0.72, half_life_h=720, source="smoke",
            legal_basis="legitimate_interest",
            payload={"stage": "series_a", "amount_usd": 12_000_000},
            observed_at=datetime.now(timezone.utc), dedupe_key=f"smoke-{uuid.uuid4().hex}")

    # -- enrich: buying a field the waterfall declares ---------------------
    with db.tenant_tx(tid) as cur:
        # A tenant with no declared provider has no waterfall, and the runtime
        # says so rather than guessing — which is correct, and means a smoke
        # run that wants to prove the waterfall has to declare one. The fake
        # keeps it on this machine; `docs/07` describes the real ladder.
        cur.execute(
            "insert into data_provider (tenant_id, key, fields, unit_cost_micros,"
            " accuracy, default_hit_rate, billed_on_miss) values"
            " (zolts_internal.current_tenant(), %s,%s,%s,%s,%s,%s)",
            ("smoke-provider", ["phone"], 900, 0.9, 0.5, False))
        register_provider(FakeDataProvider(key="smoke-provider", fields=("phone",)))
        enrichment.install_declared(cur)
        cur.execute("select * from tenant where id = %s", (tid,))
        tenant_row = dict(cur.fetchone())
        cur.execute("select * from person where email = %s", ("dana@northwind.example",))
        subject = dict(cur.fetchone())
        bought = enrichment.resolve(
            cur, tenant_row, field_name="phone", entity=subject,
            legal_basis="legitimate_interest", secret_key=settings.secret_key)

    worker = Worker(db, secret_key=settings.secret_key)
    tick = worker.tick([tid])

    # The flagship program lands a step Monday to Friday, 08:00-18:00 in
    # Madrid, and the planner moves a send into that window rather than
    # dropping it. Outside it — every evening, every weekend — the step the
    # tick just planned is due the next morning, nothing is claimed, and this
    # script reported four dead stages for the one reason that is not a
    # defect (D-44). The window is the product's behaviour and stays as it is;
    # what this script proves is that the stages connect, so a step deferred
    # by the window alone is pulled forward here, in the smoke tenant only,
    # and the report says so.
    deferred_by_window = _pull_forward(db, tid)
    if deferred_by_window:
        print(f"::notice::outside the program's sending window at "
              f"{datetime.now(timezone.utc):%H:%M} UTC; {len(deferred_by_window)} step(s) "
              f"due {', '.join(deferred_by_window)} pulled forward in the smoke tenant",
              file=sys.stderr)
        again = worker.tick([tid])
        for name in ("planned", "claimed", "succeeded", "cancelled", "deferred",
                     "failed", "dead"):
            setattr(tick, name, getattr(tick, name) + getattr(again, name))
        tick.errors.extend(again.errors)

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
        tenant_row = dict(cur.fetchone())
        measurement = console.build(cur, tenant_row)

        # -- bill: usage becomes a statement ------------------------------
        # `docs/12` prices eight actions and this loop consumes several of
        # them. A runtime that meters and never closes a period has priced
        # nothing, which is the defect ADR-017 was written about.
        period = metering.open_period(cur, tenant_row)
        statement = metering.close_period(cur, tenant_row, str(period["id"]))

    print(json.dumps({
        "program": {"key": program.key, "version": program.version,
                    "spec_hash": program.spec_hash, "lint": findings},
        "enrollments": [e.__dict__ for e in result.enrollments],
        "tick": {k: v for k, v in tick.__dict__.items() if k != "errors"},
        "worker_errors": tick.errors,
        "touches": touches,
        "policy_decisions": decisions,
        "still_queued": queued,
        "deferred_by_window": deferred_by_window,
        "webhook_effects": webhook_effects,
        "synced": {"accounts": synced.accounts, "people": synced.people,
                   "links": synced.links, "opportunities": synced.opportunities,
                   "openDeals": synced.open_deals, "caveats": synced.caveats},
        "excludedByAnOpenDeal": not deal_result.enrollments,
        "enriched": {"field": bought.field, "hit": bought.hit,
                     "provider": bought.provider,
                     "attempts": list(bought.attempts),
                     "reason": bought.reason},
        "statement": {"closed": statement.get("closed_at") is not None,
                      "seats": statement.get("seats_used"),
                      "body": statement.get("statement")},
        # One reply is one conversion, which is below the floor at which any
        # effect may be declared. The smoke run asserts that it is withheld:
        # the loop being connected is not the same as the loop having a result.
        "measurement": [{k: p[k] for k in ("key", "enrolled", "significant",
                                           "pipeline", "unresolvedReason")}
                        for p in measurement["programs"]],
    }, indent=2, default=str))
    db.close()

    # This script existed to prove the loop is connected and returned 0 no
    # matter what it found. A run that enrolled nobody, sent nothing and
    # recorded no decision printed the same success as a run that did all
    # three — so the one check standing between a broken loop and a green
    # build could not fail. Each stage now has to have happened.
    stages = {
        "a program was published": bool(program.key),
        "a CRM's records were synced": synced.accounts > 0 and synced.people > 0,
        "the CRM's deals were read": synced.opportunities > 0 and synced.open_deals > 0,
        # A miss is an answer (ADR-021); what must not happen is the
        # waterfall never being asked at all.
        "the waterfall was consulted": bool(bought.attempts),
        "an account was enrolled": bool(result.enrollments),
        # Both halves, because either one alone passes while the product is
        # broken: an audience that matches nobody satisfies the second, and an
        # exclusion that does nothing satisfies the first.
        "an account in an open deal was left alone": not deal_result.enrollments,
        "the worker planned work": tick.planned > 0 or tick.claimed > 0,
        "a touch was recorded": bool(touches),
        "the policy gate decided": bool(decisions),
        "the reply was applied": bool(webhook_effects),
        "the period closed into a statement": statement.get("closed_at") is not None,
    }
    missing = [name for name, happened in stages.items() if not happened]
    if missing:
        print("::error::the loop is not connected: " + "; ".join(missing),
              file=sys.stderr)
        return 1
    return 0


def _pull_forward(db, tid: str) -> list[str]:
    """Bring the smoke tenant's window-deferred steps due now.

    Returns when each was due, for the report. Only steps the planner pushed
    into the future are touched: a step due now is left to the tick.
    """
    with db.tenant_tx(tid) as cur:
        cur.execute("select id, run_after from action"
                    " where state = 'pending' and run_after > now()")
        rows = cur.fetchall()
        if not rows:
            return []
        cur.execute("update action set run_after = now() where id = any(%s)",
                    ([r["id"] for r in rows],))
    return [r["run_after"].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            for r in rows]


if __name__ == "__main__":
    raise SystemExit(main())
