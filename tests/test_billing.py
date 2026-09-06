"""Turning usage into something you can charge for.

`cost_event.billed_credits` existed from the first migration and no caller ever
set it. The price list in `docs/12` was complete — eight billable actions, four
plans — and the runtime billed nothing. The column was there, the structure was
there, and every action was free.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.api.app import create_app
from runtime.provision import issue_api_key
from tests.conftest import requires_db
from zolts.billing import CREDITS, PLANS, BillingError, credits_for, plan_for, statement

DOC = Path(__file__).resolve().parent.parent / "docs" / "12-pricing-and-unit-economics.md"


@pytest.fixture
def client(db):
    return TestClient(create_app(db), raise_server_exceptions=False)


@pytest.fixture
def key(db, tenant):
    return issue_api_key(db, str(tenant["id"]), "test", []).token


# -- the price list is one thing, in one place ---------------------------

def test_the_document_and_the_code_agree_on_every_price():
    """A price that lives in prose is a price two people read differently.

    The code is the reference and the document is the claim, in that order: if
    they diverge the document is corrected.
    """
    rows = re.findall(r"^\| ([A-Za-z][^|]+?) \| ([\d.]+) \|", DOC.read_text(), re.M)
    documented = {label.strip(): Decimal(value) for label, value in rows}
    assert documented, "no credit table found in docs/12"

    by_label = {
        "Verified email enrichment": "enrich.email",
        "Mobile phone": "enrich.phone",
        "Account firmographics": "enrich.firmographics",
        "Signal check (per account per day)": "signal.check",
        "Message generation (AI)": "agent.generate",
        "Research dossier (agent)": "agent.dossier",
        "Email send": "email.send",
        "Program execution (step)": "program.step",
    }
    for label, kind in by_label.items():
        assert label in documented, f"docs/12 no longer prices '{label}'"
        assert documented[label] == CREDITS[kind], (
            f"'{label}' is {documented[label]} in docs/12 and {CREDITS[kind]} in "
            f"zolts.billing. The document is corrected, never the code")


def test_the_document_and_the_code_agree_on_every_plan():
    text = DOC.read_text()
    for plan in PLANS.values():
        row = re.search(rf"\*\*{plan.name}\*\* \| €([\d,]+) \| (\d+) \| ([\d,]+)", text)
        assert row, f"docs/12 no longer lists the {plan.name} plan"
        assert Decimal(row.group(1).replace(",", "")) == plan.platform_eur
        assert int(row.group(2)) == plan.seats
        assert Decimal(row.group(3).replace(",", "")) == plan.credits


def test_an_unpriced_action_raises_rather_than_costing_nothing():
    """A silent zero is how a whole category of usage becomes free without
    anybody deciding it should be."""
    with pytest.raises(BillingError, match="has no price"):
        credits_for("enrichment.mobile")


def test_enterprise_has_no_default_terms():
    """A number here is a number somebody eventually invoices."""
    assert plan_for("enterprise") is None
    with pytest.raises(BillingError, match="negotiated terms"):
        statement(plan_key="enterprise", consumed_credits=1, seats_used=1)


def test_fractional_prices_do_not_drift():
    """A tenth of a credit lost to float on a million signal checks is a real
    invoice being wrong."""
    assert credits_for("signal.check", 1_000_000) == Decimal("500000.0000")
    assert credits_for("program.step", 3) == Decimal("0.6000")


# -- metering ------------------------------------------------------------

@requires_db
def test_every_metered_action_carries_its_credits_and_its_period(db, tenant):
    from runtime import metering

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        cur.execute("select * from tenant where id = %s", (tid,))
        row = cur.fetchone()
        metering.meter(cur, row, kind="agent.dossier", units=2)
        cur.execute("select kind, billed_credits, billing_period_id from cost_event")
        event = cur.fetchone()

    assert event["kind"] == "agent.dossier"
    assert float(event["billed_credits"]) == 40.0
    assert event["billing_period_id"], "a cost event outside a period cannot be invoiced"


@requires_db
def test_a_tenant_has_one_open_period(db, tenant):
    from runtime import metering

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        cur.execute("select * from tenant where id = %s", (tid,))
        row = cur.fetchone()
        first = metering.open_period(cur, row)
        second = metering.open_period(cur, row)
        cur.execute("select count(*) as n from billing_period where closed_at is null")
        assert cur.fetchone()["n"] == 1
    assert first["id"] == second["id"]


@requires_db
def test_spending_stops_at_the_ceiling(db, tenant):
    """Credits are the thing being sold. Spending past the ceiling with no
    decision is how a customer on the smallest plan runs up a bill nobody
    authorised."""
    from runtime import metering

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        cur.execute("select * from tenant where id = %s", (tid,))
        row = cur.fetchone()
        assert metering.allowance(cur, row, cost="email.send").allowed

        metering.meter(cur, row, kind="email.send", units=15_000)  # a Starter plan
        blocked = metering.allowance(cur, row, cost="email.send")

    assert not blocked.allowed
    assert blocked.remaining == 0
    assert "credits are spent" in blocked.reason


@requires_db
def test_the_ceiling_accounts_for_the_action_about_to_happen(db, tenant):
    """A tenant with two credits left may send two emails and not a dossier."""
    from runtime import metering

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        cur.execute("select * from tenant where id = %s", (tid,))
        row = cur.fetchone()
        metering.meter(cur, row, kind="email.send", units=14_998)
        assert metering.allowance(cur, row, cost="email.send", units=2).allowed
        assert not metering.allowance(cur, row, cost="agent.dossier").allowed


# -- closing -------------------------------------------------------------

@requires_db
def test_closing_produces_a_statement_and_is_idempotent(db, tenant):
    from runtime import metering

    tid = str(tenant["id"])
    issue_api_key(db, tid, "seat one", [])
    with db.tenant_tx(tid) as cur:
        cur.execute("select * from tenant where id = %s", (tid,))
        row = cur.fetchone()
        metering.meter(cur, row, kind="email.send", units=20_000)
        period = metering.open_period(cur, row)
        closed = metering.close_period(cur, row, str(period["id"]))
        again = metering.close_period(cur, row, str(period["id"]))

    assert closed["closed_at"] is not None
    assert again["closed_at"] == closed["closed_at"], "closing twice must not restate"
    assert closed["statement"]["consumedCredits"] == 20_000.0
    assert closed["statement"]["overageCredits"] == 5_000.0
    assert closed["statement"]["withinPlan"] is False
    # The overage rate is contractual. Inventing one here produces an invoice
    # nobody signed.
    assert "contractual" in closed["statement"]["note"]


@requires_db
def test_closing_a_period_opens_the_next_one_on_the_next_write(db, tenant):
    from runtime import metering

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        cur.execute("select * from tenant where id = %s", (tid,))
        row = cur.fetchone()
        first = metering.open_period(cur, row)
        metering.close_period(cur, row, str(first["id"]))
        metering.meter(cur, row, kind="email.send", units=1)
        second = metering.open_period(cur, row)

    assert second["id"] != first["id"]
    assert float(second["consumed_credits"]) == 1.0


@requires_db
def test_a_closed_period_keeps_the_terms_it_was_sold(db, tenant):
    """A customer who upgrades mid-month is billed on what they were sold for
    the period they consumed."""
    from runtime import metering

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        cur.execute("select * from tenant where id = %s", (tid,))
        period = metering.open_period(cur, cur.fetchone())
        assert float(period["included_credits"]) == 15_000.0

    # Changing a plan is provisioning, not application traffic: the role that
    # serves requests cannot write to `tenant` at all, which is the isolation
    # design and not an obstacle to work around.
    with db.admin_tx() as cur:
        cur.execute("update tenant set plan = 'scale' where id = %s", (tid,))

    with db.tenant_tx(tid) as cur:
        cur.execute("select * from tenant where id = %s", (tid,))
        still = metering.open_period(cur, cur.fetchone())

    assert still["id"] == period["id"]
    assert float(still["included_credits"]) == 15_000.0, "an open period was restated"


# -- what a tenant can see -----------------------------------------------

@requires_db
def test_a_tenant_reads_its_own_consumption(db, client, key, tenant):
    from runtime import metering

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        cur.execute("select * from tenant where id = %s", (tid,))
        metering.meter(cur, cur.fetchone(), kind="agent.generate", units=10)

    body = client.get("/v1/billing/current", headers={"x-api-key": key}).json()
    assert body["plan"] == "starter"
    assert body["consumedCredits"] == 30.0
    assert body["remainingCredits"] == 14_970.0
    assert body["spending"] is True
    assert body["byKind"] == [{"kind": "agent.generate", "credits": 30.0, "events": 1}]


@requires_db
def test_statements_are_tenant_scoped(db, client, key, tenant, other_tenant):
    from runtime import metering

    for scoped in (other_tenant,):
        with db.tenant_tx(str(scoped["id"])) as cur:
            cur.execute("select * from tenant where id = %s", (scoped["id"],))
            row = cur.fetchone()
            period = metering.open_period(cur, row)
            metering.close_period(cur, row, str(period["id"]))

    assert client.get("/v1/billing/statements", headers={"x-api-key": key}).json() == []


# -- enforcement, where it has to bite -----------------------------------

@requires_db
def test_a_tenant_out_of_credits_holds_its_work_rather_than_losing_it(db, tenant, fake):
    """A tenant who has run out has not done anything wrong. Cancelling would
    discard the work; failing would burn the retry budget on a condition no
    retry can change."""
    from runtime import metering
    from runtime.engine.worker import Worker
    from tests.conftest import SECRET

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        cur.execute("select * from tenant where id = %s", (tid,))
        metering.meter(cur, cur.fetchone(), kind="email.send", units=15_000)
        cur.execute(
            "insert into action (tenant_id, kind, channel, idempotency_key, payload,"
            " state) values (%s,'email.send','email','broke-1','{}','pending')", (tid,))

    Worker(db, secret_key=SECRET).tick([tid])

    with db.tenant_tx(tid) as cur:
        cur.execute("select state, last_error, run_after from action"
                    " where idempotency_key = 'broke-1'")
        row = cur.fetchone()

    assert row["state"] == "pending", "the work was discarded rather than held"
    assert "budget" in row["last_error"]
    # Due when the period turns, not in five minutes.
    assert row["run_after"] is not None
