"""Acquisition cost, reported against the ceiling a programme declares.

Decision 45. `spec.budget.max_cost_per_meeting` was a ceiling nothing read
(D-63): the schema offered it, the runtime ignored it, and a partner quoting it
in a letter quoted a number nothing held. The registered default is that it is
**reported and never acted on** — a programme is over its cost per meeting every
day until the first one lands, so a stop here kills programmes that are working.

The figure is per *incremental* meeting. The holdout books meetings this
programme did not pay for, and dividing spend by all of them would flatter the
number by exactly the amount the holdout exists to measure.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from tests.conftest import requires_db
from zolts import controls
from zolts.report import (SIGNIFICANT, BaselineQuote, Comparison,
                          IncrementalityReport, ceiling_micros, from_mapping)

CEILING = 400_000_000  # EUR 400 a meeting, in micros
SPEND = 1_200_000.0  # 1.2M credits. A credit is a cent, so EUR 12,000.
THRIFTY = 5_000.0  # EUR 50 of credits, the same programme run cheaply
RUN_RATE_MONTHLY = 11_300_000_000  # what the customer declared at onboarding
PERIOD_DAYS = 28  # 2026-09-03 to 2026-10-01
# The customer's own spend over the period, prorated at the Gregorian mean
# month. Hand-computed here so the test fails if the formula changes, rather
# than restating whatever the code does.
RUN_RATE = round(RUN_RATE_MONTHLY * PERIOD_DAYS / (365.2425 / 12))
ALL_IN = RUN_RATE + round(SPEND * 10_000)
FIGURE = round(ALL_IN / 50)  # 50 incremental meetings
SALES_LED = Path(__file__).resolve().parents[1] / "examples/programs/01-b2b-saas-sales-led.yaml"


def _report(meetings: Comparison, **overrides) -> IncrementalityReport:
    """A report whose meeting comparison is exactly the one passed in.

    The enrolment comes from `primary`, not from `converted_by_type`, so a
    helper that took the meeting counts alone would silently measure them
    against whatever arms the fixture happened to declare. The premise is
    established here and asserted before the test sees the report.
    """
    nt, nc = meetings.treatment_enrolled, meetings.control_enrolled
    fields = dict(
        program_key="flagship", program_version="2.1.0", spec_hash="abc",
        period_start=date(2026, 9, 3), period_end=date(2026, 10, 1), holdout_pct=10.0,
        # Positive replies, of which the meetings below are a subset.
        primary=Comparison(nt, nc, min(200, nt), min(100, nc)),
        opportunities=Comparison(nt, nc, 0, 0),
        converted_by_type={"meeting": {"treatment": meetings.treatment_converted,
                                       "control": meetings.control_converted}},
        credits_by_kind={"email.send": SPEND},
        baseline=BaselineQuote(digest="f" * 64, window_start=date(2026, 6, 1),
                               window_end=date(2026, 8, 30),
                               monthly_spend_micros=11_300_000_000, meetings=9,
                               opportunities=3, cost_per_meeting_micros=3_766_666_666,
                               cost_per_opportunity_micros=11_300_000_000))
    fields.update(overrides)
    report = IncrementalityReport(**fields)
    assert report.meetings == meetings, "the fixture is not measuring what it declares"
    return report


def test_the_figure_divides_spend_by_the_meetings_the_programme_caused():
    """100 treatment meetings against 50 in an equal control arm is a
    significant lift; the increment is what the holdout says would not have
    happened. Spend is divided by that, never by the 100."""
    report = _report(Comparison(1000, 1000, 100, 50))
    increment = report.meetings.incremental_conversions
    assert increment == 50, "the increment must be net of the holdout"

    figure = report.cost_per_incremental_meeting_micros
    assert figure == FIGURE == 447_905_706, "EUR 22,395 over 50 meetings is EUR 448 each"
    assert report.cost_per_meeting_withheld_because is None

    naive = round(ALL_IN / 100)
    assert figure > naive, (
        "dividing by observed meetings rather than incremental ones would report a "
        "cheaper meeting than the programme achieved")


def test_the_figure_is_all_in_and_not_the_platform_fee_alone():
    """The number sits in the same document as a baseline cost per meeting
    built from the customer's whole go-to-market spend. Zolts credits alone
    would be EUR 240 a meeting against a baseline of EUR 1,297 and a declared
    ceiling of EUR 400: three numbers in one letter that are not the same
    measurement (decision 45)."""
    report = _report(Comparison(1000, 1000, 100, 50))
    assert report.run_rate_spend_micros == RUN_RATE
    assert report.acquisition_spend_micros == RUN_RATE + 12_000_000_000
    platform_only = round(SPEND * 10_000 / 50)
    assert report.cost_per_incremental_meeting_micros > platform_only * 1.8, (
        "the figure must carry the customer's own spend, not the platform fee alone")


def test_a_credit_is_a_cent_and_the_conversion_says_so():
    """A hundredfold error in a number a CFO reads. 5,000 credits is EUR 50.00,
    so it adds 50,000,000 micros to the period's spend and not 5,000,000,000."""
    report = _report(Comparison(1000, 1000, 100, 50),
                     credits_by_kind={"email.send": THRIFTY})
    assert report.acquisition_spend_micros == RUN_RATE + 50_000_000
    assert report.acquisition_spend_micros != RUN_RATE + 5_000_000_000, (
        "a credit read as a euro would add EUR 5,000 of platform fee for EUR 50 of it")
    assert report.cost_per_incremental_meeting_micros == round((RUN_RATE + 50_000_000) / 50)


def test_the_run_rate_is_prorated_by_days_and_never_by_a_thirty_day_month():
    """A 30-day month loses five and a half days a year. Twelve periods of a
    signed report would then sum to less than the run-rate the customer
    declared, and every one of them would read cheap."""
    report = _report(Comparison(1000, 1000, 100, 50))
    assert report.period_days == PERIOD_DAYS
    assert report.run_rate_spend_micros == RUN_RATE
    assert report.run_rate_spend_micros != round(RUN_RATE_MONTHLY * PERIOD_DAYS / 30)

    whole_year = sum(
        _report(Comparison(1000, 1000, 100, 50),
                period_start=date(2026, m, 1),
                period_end=date(2026 + (m == 12), m % 12 + 1, 1)).run_rate_spend_micros
        for m in range(1, 13))
    # 2026 is a common year, so twelve of its months are 365 days and not the
    # 365.2425 the mean month is built from: the sum is the declared year scaled
    # by the days that actually elapsed, which is the whole point of prorating.
    assert abs(whole_year - RUN_RATE_MONTHLY * 12 * 365 / 365.2425) < 12

    # A 30-day divisor charges 365 days as 12.17 months rather than 12, so every
    # period reads expensive and a year of them overstates the declared run-rate
    # by five days. Wrong in the direction that makes the product look worse,
    # which is why it survives review and still may not ship.
    thirty_day_year = sum(round(RUN_RATE_MONTHLY * d / 30)
                          for d in (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31))
    assert thirty_day_year > RUN_RATE_MONTHLY * 12 > whole_year


def test_no_baseline_means_no_figure_and_the_document_says_which():
    """Without a declared baseline the customer's own spend is unknown, and a
    cost per meeting made of the platform fee alone would be off by an order of
    magnitude in the flattering direction."""
    report = _report(Comparison(1000, 1000, 100, 50), baseline=None,
                     max_cost_per_meeting_micros=CEILING)
    assert report.meetings.verdict == SIGNIFICANT, "the comparison is not the problem"
    assert report.run_rate_spend_micros is None
    assert report.acquisition_spend_micros is None
    assert report.cost_per_incremental_meeting_micros is None
    assert report.over_cost_per_meeting_ceiling is None
    assert "no baseline was declared" in report.cost_per_meeting_withheld_because
    assert "no baseline was declared" in report.render_markdown()


def test_the_figure_is_withheld_with_a_reason_when_the_arms_cannot_resolve():
    """The same rule the pipeline figure follows: an increment under the
    detectable effect is noise wearing an integer."""
    report = _report(Comparison(1000, 1000, 1, 1))
    assert report.cost_per_incremental_meeting_micros is None
    assert "meeting comparison is not resolvable" in report.cost_per_meeting_withheld_because
    assert "fewer than 5 conversions" in report.cost_per_meeting_withheld_because


def test_a_lift_under_the_detectable_effect_earns_no_figure():
    """60 meetings against 50 is a lift, and it is inside the noise. A cost per
    meeting computed from it would price an effect the sample cannot see."""
    report = _report(Comparison(1000, 1000, 60, 50))
    assert report.meetings.verdict != SIGNIFICANT
    assert report.cost_per_incremental_meeting_micros is None
    assert report.cost_per_meeting_withheld_because == (
        "the meeting comparison is not significant")


def test_no_meeting_at_all_is_withheld_rather_than_infinite():
    report = _report(Comparison(1000, 1000, 0, 0))
    assert report.cost_per_incremental_meeting_micros is None
    assert report.cost_per_meeting_withheld_because


def test_a_significant_meeting_comparison_always_caused_a_meeting():
    """The reason there is one withholding sentence and not two.

    A second sentence for a zero increment would be a branch no input reaches:
    the five-conversion floor keeps the detectable effect above 0.5 divided by
    the treatment arm, so a lift that clears it cannot round to nothing. Proved
    here rather than asserted in a comment, because the register already holds
    nine guards that pass when you break what they guard.
    """
    checked = 0
    for nt in (5, 6, 8, 13, 40, 200, 1000):
        for nc in (5, 6, 8, 13, 40, 200, 1000):
            for tc in range(5, min(nt, 40) + 1):
                for cc in range(5, min(nc, 40) + 1):
                    comparison = Comparison(nt, nc, tc, cc)
                    if comparison.verdict != SIGNIFICANT:
                        continue
                    checked += 1
                    assert comparison.incremental_conversions >= 1, comparison
    assert checked > 1000, "the sweep found too few significant cases to prove anything"


def test_the_breach_flag_is_none_and_never_false_without_a_figure():
    """False would read as *within budget* on a programme that has produced no
    number to judge, which is the more expensive of the two wrong answers."""
    no_figure = _report(Comparison(1000, 1000, 1, 1), max_cost_per_meeting_micros=CEILING)
    assert no_figure.over_cost_per_meeting_ceiling is None

    no_ceiling = _report(Comparison(1000, 1000, 100, 50))
    assert no_ceiling.cost_per_incremental_meeting_micros is not None
    assert no_ceiling.over_cost_per_meeting_ceiling is None


def test_the_breach_flag_reads_both_ways():
    """The same programme and the same meetings, run at two spend levels
    against one declared ceiling."""
    over = _report(Comparison(1000, 1000, 100, 50), max_cost_per_meeting_micros=CEILING)
    assert over.cost_per_incremental_meeting_micros == 447_905_706
    assert over.over_cost_per_meeting_ceiling is True, "EUR 448 is above EUR 400"

    within = _report(Comparison(1000, 1000, 100, 50),
                     credits_by_kind={"email.send": THRIFTY},
                     max_cost_per_meeting_micros=CEILING)
    assert within.cost_per_incremental_meeting_micros == 208_905_706
    assert within.over_cost_per_meeting_ceiling is False, "EUR 209 is inside EUR 400"


def test_a_ceiling_of_zero_is_a_ceiling_and_not_an_absence():
    """Zero is falsy and a declared zero is still a declaration. A programme
    that declares it is above it, and the report says so rather than going
    quiet."""
    report = _report(Comparison(1000, 1000, 100, 50), max_cost_per_meeting_micros=0)
    assert report.over_cost_per_meeting_ceiling is True
    assert "ceiling of" in report.render_markdown()


def test_the_ceiling_and_the_figure_are_both_covered_by_the_digest():
    """A letter quotes the digest. A ceiling the digest does not cover is a
    number the signature does not stand behind."""
    base = _report(Comparison(1000, 1000, 100, 50), max_cost_per_meeting_micros=CEILING)
    moved = _report(Comparison(1000, 1000, 100, 50), max_cost_per_meeting_micros=CEILING + 1)
    assert base.digest() != moved.digest()

    canonical = base.canonical()
    for key in ("max_cost_per_meeting_micros", "cost_per_incremental_meeting_micros",
                "cost_per_meeting_withheld_because", "over_cost_per_meeting_ceiling"):
        assert key in canonical, f"{key} is not covered by the digest"


def test_the_document_states_the_ceiling_and_that_it_is_never_enforced():
    """`docs/22` asks for the consequence in the reader's own document, not
    only in a register they do not hold."""
    report = _report(Comparison(1000, 1000, 100, 50),
                     max_cost_per_meeting_micros=CEILING)
    rendered = report.render_markdown()
    assert "## Cost per meeting" in rendered
    assert "incremental meetings" in rendered
    assert "above it" in rendered
    assert "never enforced" in rendered
    assert "All in, on the same basis as the baseline" in rendered
    assert "nothing re-measures it" in rendered, (
        "the run-rate is an assumption and the reader must be told")

    within = _report(Comparison(1000, 1000, 100, 50),
                     credits_by_kind={"email.send": THRIFTY},
                     max_cost_per_meeting_micros=CEILING).render_markdown()
    assert "within it" in within

    withheld = _report(Comparison(1000, 1000, 1, 1)).render_markdown()
    assert "Not reported: the meeting comparison" in withheld
    assert "ceiling" not in withheld.split("## Cost per meeting")[1], (
        "a report with no declared ceiling must not invent one")


@pytest.mark.parametrize("micros", [0, 1, 999_999, 400_000_000])
def test_the_ceiling_survives_the_round_trip_into_the_document(micros):
    report = _report(Comparison(1000, 1000, 100, 50), max_cost_per_meeting_micros=micros)
    assert report.canonical()["max_cost_per_meeting_micros"] == micros


def test_the_ceiling_survives_a_rebuild_from_the_canonical_json():
    """The report's whole claim is that either party can rebuild it from the
    JSON and get the same hash. A field the canonical form emits and the
    rebuild drops breaks that silently: the ceiling comes back None, the digest
    differs, and the two sides of a signed document disagree.
    """
    original = _report(Comparison(1000, 1000, 100, 50),
                       max_cost_per_meeting_micros=CEILING)
    rebuilt = from_mapping(original.canonical())
    assert rebuilt.max_cost_per_meeting_micros == CEILING
    assert rebuilt.digest() == original.digest()
    assert rebuilt.render_markdown() == original.render_markdown()

    absent = from_mapping(_report(Comparison(1000, 1000, 100, 50)).canonical())
    assert absent.max_cost_per_meeting_micros is None, "no ceiling is not a ceiling of 0"

    zero = from_mapping(_report(Comparison(1000, 1000, 100, 50),
                                max_cost_per_meeting_micros=0).canonical())
    assert zero.max_cost_per_meeting_micros == 0, "a declared 0 is not an absent ceiling"
    assert zero.digest() != absent.digest()


# -- what a programme declares, in the unit the report holds ---------------

@pytest.mark.parametrize("declared, micros", [
    (180, 180_000_000), (0, 0), (0.5, 500_000), (1234.56, 1_234_560_000), (None, None)])
def test_a_ceiling_declared_in_euros_reaches_the_report_in_micros(declared, micros):
    """Budgets are typed in euros and every money field the runtime holds is in
    micros. A ceiling that crossed that boundary unconverted would be a
    millionfold error in the direction that never fires."""
    budget = {} if declared is None else {"max_cost_per_meeting": declared}
    assert ceiling_micros({"budget": budget}) == micros


def test_a_programme_with_no_budget_block_declares_no_ceiling():
    for spec in (None, {}, {"budget": None}, {"budget": {}}):
        assert ceiling_micros(spec) is None


def test_the_shipped_programme_that_declares_a_ceiling_carries_it_through():
    """`examples/programs/01-b2b-saas-sales-led.yaml` is the document a partner
    reads. Its ceiling is the one the register said nothing held."""
    spec = yaml.safe_load(SALES_LED.read_text())["spec"]
    declared = spec["budget"]["max_cost_per_meeting"]
    assert ceiling_micros(spec) == round(float(declared) * 1_000_000)


# -- the registry ---------------------------------------------------------

def test_a_reported_ceiling_is_never_counted_as_an_enforced_one():
    """The distinction the registry exists for. Decision 45 makes the figure
    visible; it does not make the engine hold the limit, and a registry that
    blurred that would re-create D-63 with better documentation."""
    ceiling = controls.BY_PATH["spec.budget.max_cost_per_meeting"]
    assert ceiling.reported, "decision 45 puts the figure on the report"
    assert not ceiling.honoured, "reporting a ceiling is not enforcing it"
    assert ceiling in controls.NOT_HONOURED
    assert ceiling not in controls.HONOURED
    assert "never acted on" in ceiling.consequence


def test_every_reported_control_names_where_the_figure_appears():
    for control in controls.REPORTED:
        assert control.reported_by and "/" in control.reported_by, (
            f"{control.path} claims to be reported without naming where")


def test_a_reported_control_still_appears_in_a_programme_s_unenforced_list():
    """An operator publishing the programme must still be told the engine does
    not hold this. `scripts/validate.py` says *reported and not enforced*, which
    is two facts; dropping it from the list would leave only the flattering one.
    """
    spec = yaml.safe_load(SALES_LED.read_text())["spec"]
    paths = {c.path for c in controls.unenforced(spec)}
    assert "spec.budget.max_cost_per_meeting" in paths


# -- what the runtime counts ----------------------------------------------

@requires_db
def test_the_meeting_arm_is_counted_inside_the_metric_window(db, tenant):
    """The composition breakdown was counted over any time after enrolment
    while the primary number and the opportunity arms beside it were counted
    inside the declared window. That was tolerable while it only disclosed what
    the primary number was made of; dividing it into money (decision 45) makes
    a cost per meeting computed over a different span from the lift it sits
    beside. Both meetings below are real; only one is this window's.
    """
    import json

    from runtime import metering, reporting

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        spec = {"experiment": {"holdout_pct": 10}}  # any_conversion_90d, the default
        cur.execute(
            "insert into program (tenant_id, key, version, spec, spec_hash, status)"
            " values (%s,'meetings','1.0.0',%s,'h-meetings','live') returning id",
            (tid, json.dumps(spec)))
        program_id = str(cur.fetchone()["id"])

        for days_after in (2, 150):
            cur.execute(
                "insert into enrollment (tenant_id, program_id, entity_type, entity_id,"
                " variant, state, entered_at) values (%s,%s,'account',gen_random_uuid(),"
                " 'treatment','running', now() - interval '200 days') returning id",
                (tid, program_id))
            enrollment_id = str(cur.fetchone()["id"])
            cur.execute(
                "insert into outcome (tenant_id, enrollment_id, type, occurred_at,"
                " source, dedupe_key) values (%s,%s,'meeting',"
                " now() - interval '200 days' + %s, 'test', %s)",
                (tid, enrollment_id, timedelta(days=days_after), f"k-{uuid.uuid4().hex}"))

        cur.execute("select * from tenant where id = %s", (tid,))
        row = dict(cur.fetchone())
        period = metering.open_period(cur, row)
        closed = metering.close_period(cur, row, str(period["id"]))
        [frozen] = reporting.freeze_all(cur, row, closed, frozen_by="test")

    booked = frozen["body"]["converted_by_type"].get("meeting", {})
    assert booked.get("treatment") == 1, (
        "a meeting 150 days after enrolment is outside a 90-day window and must not "
        "be counted, however real it is")
    assert frozen["body"]["metric_window_days"] == 90


@requires_db
def test_the_ceiling_travels_from_the_spec_to_the_operator_s_screen(db, tenant):
    """The whole chain, because every link of it has been the defect at least
    once: a ceiling in a document, converted to micros, carried into a frozen
    report, and rendered on the panel the operator reads. D-63 was this chain
    with nothing at the second link.
    """
    import json

    from runtime import metering, reporting
    from runtime.api import console
    from runtime.repo import baseline as baseline_repo
    from tests.test_baseline import DECLARED
    from zolts.baseline import from_mapping as baseline_from_mapping

    tid = str(tenant["id"])
    spec = {"experiment": {"holdout_pct": 10},
            "budget": {"monthly_credits": 40000, "max_cost_per_meeting": 400}}
    with db.tenant_tx(tid) as cur:
        cur.execute(
            "insert into program (tenant_id, key, version, spec, spec_hash, status)"
            " values (%s,'ceilinged','1.0.0',%s,'h-ceiling','live') returning id",
            (tid, json.dumps(spec)))
        program_id = str(cur.fetchone()["id"])
        for variant, booked in (("treatment", 90), ("control", 20)):
            for i in range(400):
                cur.execute(
                    "insert into enrollment (tenant_id, program_id, entity_type,"
                    " entity_id, variant, state, entered_at) values (%s,%s,'account',"
                    " gen_random_uuid(),%s,'running', now()) returning id",
                    (tid, program_id, variant))
                enrollment_id = str(cur.fetchone()["id"])
                if i < booked:
                    cur.execute(
                        "insert into outcome (tenant_id, enrollment_id, type,"
                        " occurred_at, source) values (%s,%s,'meeting',now(),'t')",
                        (tid, enrollment_id))
        cur.execute("select * from tenant where id = %s", (tid,))
        row = dict(cur.fetchone())
        baseline_repo.freeze(cur, tid, baseline_from_mapping(DECLARED),
                             signed_by="R. Ortega", captured_by="operator")
        period = metering.open_period(cur, row)
        closed = metering.close_period(cur, row, str(period["id"]))
        [frozen] = reporting.freeze_all(cur, row, closed, frozen_by="operator")
        view = console.build(cur, row)

    body = frozen["body"]
    assert body["max_cost_per_meeting_micros"] == 400_000_000, "EUR 400 declared, in micros"
    assert body["converted_by_type"]["meeting"] == {"treatment": 90, "control": 20}
    # 90/400 against 20/400 over 400 treated: the holdout's own meetings removed.
    assert body["cost_per_incremental_meeting_micros"] == round(
        body["acquisition_spend_micros"] / 70)
    assert body["run_rate_spend_micros"] is not None, "the baseline supplies the run-rate"

    [program] = [p for p in view["programs"] if p["key"] == "ceilinged"]
    [shown] = program["reports"]
    assert shown["costPerMeetingCeilingEur"] == 400.0
    assert shown["costPerMeetingEur"] == round(
        body["cost_per_incremental_meeting_micros"] / 1_000_000, 2)
    assert shown["overCostCeiling"] is body["over_cost_per_meeting_ceiling"]
    assert shown["costPerMeetingWithheld"] is None


# -- the documents ---------------------------------------------------------

DOCS = Path(__file__).resolve().parents[1] / "docs"


def test_no_document_claims_the_per_meeting_ceiling_stops_anything():
    """`docs/04` said the budget block's ceilings stop execution automatically.
    One of the five does, for one of eight priced actions (D-63), and this one
    never will by decision 45. A document that promises enforcement is worse
    than a field nobody reads: the customer stops looking."""
    dsl_doc = (DOCS / "04-gtm-program-dsl.md").read_text()
    assert "Execution stops automatically at the ceiling" not in dsl_doc
    budget_row = next(ln for ln in dsl_doc.splitlines() if ln.startswith("| `budget` |"))
    assert "reported and never enforced" in budget_row
    assert "decision 45" in budget_row


def test_the_measurement_document_states_the_basis_and_not_the_platform_fee():
    """A formula in a document that disagrees with the one in the code is the
    version a partner quotes."""
    measurement = (DOCS / "10-measurement-and-incrementality.md").read_text()
    row = next(ln for ln in measurement.splitlines()
               if ln.startswith("| **Cost per incremental meeting**"))
    assert "credits ÷ incremental meetings" not in row, (
        "the platform fee alone is not the acquisition cost")
    assert "prorated over the period from the frozen baseline" in row
    assert "only when that comparison is significant" in row
    assert "yes, or the reason it is withheld |" in row


def test_the_decision_register_records_the_default_as_built():
    """A registered default that has been implemented and still reads as a
    plan is how a decision gets reopened and rebuilt."""
    register = (DOCS / "18-decision-register.md").read_text()
    forty_five = next(ln for ln in register.splitlines() if ln.startswith("| 45 |"))
    assert "built" in forty_five
    assert "reported and still not enforced" in forty_five
    assert any(ln.startswith("| 46 |") for ln in register.splitlines()), (
        "the basis the figure is denominated in is its own decision (decision 46)")


# -- the credits table -----------------------------------------------------

@pytest.mark.parametrize("credits, printed", [
    (8.0, "8"), (40.0, "40"), (0.2, "0.2"), (1_000.0, "1,000"),
    (1_200_000.0, "1,200,000"), (1_234_567.89, "1,234,567.89"), (0.0, "0")])
def test_credits_are_printed_for_a_person_and_never_in_exponential(credits, printed):
    """D-70. The table used `:g`, which switches to exponential above six
    significant digits: a programme billing 1,200,000 credits printed
    `1.2e+06` in a document a partner signs. Small programmes never reached
    the threshold, which is why it survived — and the cost per meeting now
    makes a large credit total load-bearing rather than incidental.
    """
    from zolts.report import _credits

    assert _credits(credits) == printed
    assert "e+" not in _credits(credits)


def test_the_cost_table_in_a_rendered_report_carries_no_exponential():
    report = _report(Comparison(1000, 1000, 100, 50))
    table = report.render_markdown().split("## What it cost")[1]
    assert "e+" not in table
    assert "| email.send | 1,200,000 |" in table
    assert "| **Total** | **1,200,000** |" in table


# -- the console's three states -------------------------------------------

CONSOLE = Path(__file__).resolve().parents[1] / "design" / "console.html"


def test_the_console_paints_no_figure_as_undecided_and_never_as_within_budget():
    """The same rule as `over_cost_per_meeting_ceiling` returning None rather
    than False, one layer up. `overCostCeiling ? "fail" : "pass"` is true for a
    null, so a period that produced no cost per meeting at all would carry the
    green tick and the words *within budget*. That is the more expensive of the
    two wrong answers, and it is the one a surface reaches for by default.
    """
    markup = CONSOLE.read_text()
    assert '(breach == null ? "open" : breach ? "fail" : "pass")' in markup, (
        "the ceiling note must distinguish no figure from within the ceiling")
    assert '.note.open{' in markup, "the third tone needs a rule of its own"
    assert 'first.overCostCeiling ? "fail" : "pass"' not in markup


def test_the_console_states_the_basis_and_the_rule_beside_the_figure():
    """`docs/22` asks for the consequence in the surface the reader is on."""
    markup = CONSOLE.read_text()
    assert "Reported, never enforced." in markup
    assert "Cost per meeting is all in" in markup
    assert "no cost per meeting" in markup, "an absent figure is never a bare dash (D-28)"
