"""Turning usage into something you can charge for.

`cost_event.billed_credits` existed from the first migration and no caller ever
set it. The price list in `docs/12` was complete — eight billable actions, four
plans — and the runtime billed nothing. The column was there, the structure was
there, and every action was free.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.api.app import create_app
from runtime.provision import issue_api_key
from tests.conftest import SECRET, requires_db
from zolts.billing import (CREDIT_TIERS, CREDITS, EUR_PER_SEAT, PLANS, BillingError,
                           check_tiers, credits_for, plan_for, price_credits, statement)

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


def test_the_document_and_the_code_agree_on_the_credit_ladder():
    """The ladder is priced in `docs/12` and was never charged. It is re-read
    here so the two cannot drift apart quietly."""
    text = DOC.read_text()
    row = re.search("0-100k \u2192 \u20ac(\\d+\\.\\d+); 100k-500k \u2192 \u20ac(\\d+\\.\\d+); "
                    ">500k \u2192 \u20ac(\\d+\\.\\d+)", text)
    assert row, "docs/12 no longer states the volume ladder"
    documented = [Decimal(row.group(n)) for n in (1, 2, 3)]
    assert [rate for _, rate in CREDIT_TIERS] == documented
    assert [upper for upper, _ in CREDIT_TIERS] == [Decimal("100000"), Decimal("500000"), None]


def test_the_document_and_the_code_agree_on_the_seat_price():
    row = re.search("Additional seat: \u20ac([\\d,]+)/month", DOC.read_text())
    assert row, "docs/12 no longer prices an additional seat"
    assert Decimal(row.group(1).replace(",", "")) == EUR_PER_SEAT


def test_the_ladder_is_graduated_and_not_a_flat_rate_chosen_by_volume():
    """Each band prices only the credits inside it."""
    assert price_credits(50_000) == Decimal("500.00")            # 50k x 0.010
    assert price_credits(100_000) == Decimal("1000.00")
    assert price_credits(300_000) == Decimal("2600.00")          # 1000 + 200k x 0.008
    assert price_credits(600_000) == Decimal("4800.00")          # + 100k x 0.006
    assert price_credits(0) == Decimal("0.00")
    assert price_credits(-5) == Decimal("0.00")


def test_consuming_more_never_lowers_the_bill():
    """The property that rules out the other reading of the ladder.

    A flat rate chosen by total volume reprices everything at each boundary, so
    crossing 100,000 credits would make the invoice *fall*. Spending more money
    to pay less is not a discount, it is a defect, and this is the test that
    would have caught it.
    """
    previous = Decimal("-1")
    for credits in range(0, 700_001, 4_999):
        now = price_credits(credits)
        assert now >= previous, f"the bill fell at {credits} credits"
        previous = now


def test_the_blended_rate_stays_inside_the_ladder():
    """A tenant seeing EUR 0.0094 on an invoice that lists EUR 0.008 has not been
    overcharged, and the statement says so rather than a support agent."""
    cheapest = min(rate for _, rate in CREDIT_TIERS)
    dearest = max(rate for _, rate in CREDIT_TIERS)
    for consumed in (60_001, 100_000, 260_000, 1_000_000):
        blended = statement(plan_key="growth", consumed_credits=consumed,
                            seats_used=1).blended_eur_per_credit
        assert cheapest <= blended <= dearest
    assert statement(plan_key="growth", consumed_credits=10,
                     seats_used=1).blended_eur_per_credit is None


def test_a_ladder_with_a_gap_or_a_ceiling_is_refused():
    """A band that stops pricing leaves volume above it free, and a ladder that
    does not ascend prices some of it twice."""
    with pytest.raises(BillingError, match="open-ended"):
        check_tiers(((Decimal("100"), Decimal("0.01")),))
    with pytest.raises(BillingError, match="ascend"):
        check_tiers(((Decimal("500"), Decimal("0.01")),
                     (Decimal("100"), Decimal("0.008")), (None, Decimal("0.006"))))
    with pytest.raises(BillingError, match="at least one band"):
        check_tiers(())


def test_seats_over_the_plan_are_charged():
    """Counted and not charged for exactly as long as credits were."""
    billed = statement(plan_key="growth", consumed_credits=0, seats_used=8)
    assert billed.seats_over == 3
    assert billed.seats_eur == Decimal("270.00")
    assert billed.total_eur == Decimal("1760")
    assert billed.within_plan is False


def test_a_period_inside_the_plan_costs_the_platform_fee_and_nothing_else():
    billed = statement(plan_key="scale", consumed_credits=199_999, seats_used=12)
    assert billed.within_plan
    assert billed.total_eur == Decimal("3900")


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


@requires_db
def test_overage_is_unreachable_until_the_ceiling_is_raised(db, tenant):
    """The default ceiling is the plan, so nobody overspends by accident.

    This is the half of `docs/18` I6 the runtime had: a hard stop. The other
    half is that it must be raisable, or the overage priced in `docs/12` is
    revenue the product cannot earn.
    """
    from runtime import metering

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        cur.execute("select * from tenant where id = %s", (tid,))
        row = cur.fetchone()
        metering.meter(cur, row, kind="email.send", units=15_000)
        capped = metering.allowance(cur, row, cost="email.send")

    assert not capped.allowed
    assert capped.ceiling == Decimal("15000.00")
    assert "credits are spent" in capped.reason

    # Raising it is provisioning, like changing a plan: the role that serves
    # requests cannot write to `tenant` at all.
    with db.admin_tx() as cur:
        cur.execute("update tenant set credit_ceiling = 20000 where id = %s", (tid,))

    with db.tenant_tx(tid) as cur:
        cur.execute("select * from tenant where id = %s", (tid,))
        row = cur.fetchone()
        raised = metering.allowance(cur, row, cost="email.send")
        metering.meter(cur, row, kind="email.send", units=5_000)
        stopped = metering.allowance(cur, row, cost="email.send")

    assert raised.allowed, "the ceiling was raised and the tenant is still stopped"
    assert raised.overage == 0
    assert not stopped.allowed
    assert stopped.overage == Decimal("5000")
    assert "ceiling is reached" in stopped.reason, (
        "a tenant stopped at a raised ceiling is told they spent their plan")


@requires_db
def test_the_alert_fires_before_the_ceiling_does(db, tenant):
    """A customer who finds out at the ceiling found out too late to do
    anything but stop. `docs/18` I6 puts the alert at 80%."""
    from runtime import metering

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        cur.execute("select * from tenant where id = %s", (tid,))
        row = cur.fetchone()
        assert not metering.allowance(cur, row).alerting
        metering.meter(cur, row, kind="email.send", units=11_999)   # 79.99%
        assert not metering.allowance(cur, row).alerting
        metering.meter(cur, row, kind="email.send", units=1)        # 80.00%
        quiet = metering.allowance(cur, row)

    assert quiet.alerting
    assert quiet.share_used == Decimal("0.8000")
    assert quiet.allowed, "the alert is a warning, not the stop"


@requires_db
def test_a_tenant_sees_what_the_period_costs_before_the_invoice(db, client, key, tenant):
    from runtime import metering

    tid = str(tenant["id"])
    with db.admin_tx() as cur:
        cur.execute("update tenant set credit_ceiling = 30000 where id = %s", (tid,))
    with db.tenant_tx(tid) as cur:
        cur.execute("select * from tenant where id = %s", (tid,))
        metering.meter(cur, cur.fetchone(), kind="email.send", units=18_000)

    body = client.get("/v1/billing/current", headers={"x-api-key": key}).json()
    assert body["creditCeiling"] == 30_000.0
    assert body["overageCredits"] == 3_000.0
    assert body["overageEur"] == 30.0
    assert body["projectedTotalEur"] == 520.0
    assert body["alerting"] is False   # 18,000 of 30,000 is 60%
    assert body["spending"] is True


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
    # 5,000 credits of overage, all in the first band, plus the one seat this
    # test issued beyond the two a Starter plan includes... which is none.
    assert closed["statement"]["overageEur"] == 50.0
    assert closed["statement"]["seatsOver"] == 0
    assert closed["statement"]["totalEur"] == 540.0


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


@requires_db
def test_an_enterprise_contract_carries_its_own_ladder_and_seat_price(db, tenant):
    """An enterprise tenant that negotiated its own rates must not be billed on
    the list price. The contract is the terms; the price list is a default."""
    from runtime import metering

    tid = str(tenant["id"])
    with db.admin_tx() as cur:
        cur.execute(
            "update tenant set plan = 'enterprise', negotiated_terms = %s where id = %s",
            (json.dumps({"platform_eur": 8000, "seats": 20, "credits": 300_000,
                         "seat_eur": 60,
                         "credit_tiers": [[200_000, 0.007], [None, 0.005]]}), tid))

    with db.tenant_tx(tid) as cur:
        cur.execute("select * from tenant where id = %s", (tid,))
        row = cur.fetchone()
        metering.meter(cur, row, kind="email.send", units=550_000)
        period = metering.open_period(cur, row)
        closed = metering.close_period(cur, row, str(period["id"]))

    billed = closed["statement"]
    assert billed["includedCredits"] == 300_000.0
    assert billed["overageCredits"] == 250_000.0
    # 200,000 at 0.007 and 50,000 at 0.005, on the contract's ladder and not
    # the standard one, which would have charged 2,400.
    assert billed["overageEur"] == 1650.0
    assert billed["seatEur"] == 60.0


@requires_db
def test_a_negotiated_ladder_with_a_hole_in_it_is_refused(db, tenant):
    """Terms typed into a contract record reach the invoice unmediated. A
    ladder whose last band has a ceiling leaves everything above it free."""
    from runtime import metering
    from zolts.billing import BillingError

    tid = str(tenant["id"])
    with db.admin_tx() as cur:
        cur.execute(
            "update tenant set plan = 'enterprise', negotiated_terms = %s where id = %s",
            (json.dumps({"platform_eur": 8000, "seats": 20, "credits": 300_000,
                         "credit_tiers": [[200_000, 0.007]]}), tid))

    with db.tenant_tx(tid) as cur:
        cur.execute("select * from tenant where id = %s", (tid,))
        with pytest.raises(BillingError, match="open-ended"):
            metering.open_period(cur, cur.fetchone())


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


# -- the operator's path to a raised ceiling -----------------------------

@requires_db
def test_the_ceiling_is_raisable_from_the_command_line(db, tenant, monkeypatch):
    """A ceiling nothing can raise is a ceiling that cannot be raised.

    The runtime already had a hard stop and no way past it, which is how the
    overage `docs/12` prices came to be unreachable. A feature whose only
    operator interface is hand-written SQL is the same defect wearing a column.
    """
    from runtime import cli
    from tests.conftest import APP_URL, OWNER_URL

    monkeypatch.setenv("ZOLTS_DATABASE_URL", OWNER_URL)
    monkeypatch.setenv("ZOLTS_APP_DATABASE_URL", APP_URL)
    monkeypatch.setenv("ZOLTS_SECRET_KEY", SECRET)
    tid = str(tenant["id"])

    assert cli.main(["set-terms", "--tenant", tid, "--plan", "growth",
                     "--credit-ceiling", "80000"]) == 0
    with db.tenant_tx(tid) as cur:
        cur.execute("select plan, credit_ceiling from tenant where id = %s", (tid,))
        raised = cur.fetchone()
    assert raised["plan"] == "growth"
    assert raised["credit_ceiling"] == Decimal("80000.00")

    # 'plan' puts it back to the allowance rather than to an arbitrary number.
    assert cli.main(["set-terms", "--tenant", tid, "--credit-ceiling", "plan"]) == 0
    with db.tenant_tx(tid) as cur:
        cur.execute("select credit_ceiling from tenant where id = %s", (tid,))
        assert cur.fetchone()["credit_ceiling"] is None

    assert cli.main(["set-terms", "--tenant", tid]) == 2, "a no-op must not look like a change"


@requires_db
def test_terms_that_cannot_be_billed_are_refused_before_they_are_stored(
        db, tenant, monkeypatch, tmp_path):
    """The check runs inside the transaction, so a refused change leaves
    nothing half-applied — the tenant keeps the plan it could be billed on."""
    from runtime import cli
    from tests.conftest import APP_URL, OWNER_URL

    monkeypatch.setenv("ZOLTS_DATABASE_URL", OWNER_URL)
    monkeypatch.setenv("ZOLTS_APP_DATABASE_URL", APP_URL)
    monkeypatch.setenv("ZOLTS_SECRET_KEY", SECRET)
    tid = str(tenant["id"])

    terms = tmp_path / "terms.json"
    terms.write_text(json.dumps({"platform_eur": 8000, "seats": 20,
                                 "credits": 300_000,
                                 "credit_tiers": [[200_000, 0.007]]}))

    with pytest.raises(SystemExit, match="cannot be billed"):
        cli.main(["set-terms", "--tenant", tid, "--plan", "enterprise",
                  "--negotiated", str(terms)])

    with db.tenant_tx(tid) as cur:
        cur.execute("select plan, negotiated_terms from tenant where id = %s", (tid,))
        untouched = cur.fetchone()
    assert untouched["plan"] == "starter"
    assert untouched["negotiated_terms"] is None
