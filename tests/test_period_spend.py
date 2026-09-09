"""A period's own go-to-market spend, declared rather than assumed.

Decision 46 put the cost per incremental meeting on an all-in basis and paid
for it with an assumption: the tenant's monthly spend, declared once at
onboarding and prorated by days, with nothing re-measuring it. The report
disclosed that, which is honest and is not the same as being right. A partner
who hires two more people reads a figure wrong in a direction nobody can see,
because there is no second number for it to disagree with.

This is the registered follow-on. A declaration for the period replaces the
assumption; a period with none still falls back to the run-rate, and the report
names which of the two it used — the same figure means different things, and a
reader cannot tell by looking at it.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from tests.conftest import requires_app_role, requires_db
from zolts.baseline import SPEND_FIELDS
from zolts.report import (DECLARED, KEYS_ADDED_IN, RUN_RATE, SCHEMA_VERSION,
                          BaselineQuote, Comparison, IncrementalityReport,
                          from_mapping)
from zolts.spend import PeriodSpend, SpendError
from zolts.spend import from_mapping as spend_from_mapping

WINDOW = {"window_start": date(2026, 9, 3), "window_end": date(2026, 10, 1)}
BASELINE = BaselineQuote(
    digest="f" * 64, window_start=date(2026, 6, 1), window_end=date(2026, 8, 30),
    monthly_spend_micros=11_300_000_000, meetings=9, opportunities=3,
    cost_per_meeting_micros=3_766_666_666, cost_per_opportunity_micros=11_300_000_000)
# The onboarding run-rate over this period's 28 days, hand-computed.
RUN_RATE_MICROS = round(11_300_000_000 * 28 / (365.2425 / 12))


def _spend(**overrides) -> PeriodSpend:
    fields = dict(WINDOW, spend_tools_micros=1_400_000_000,
                  spend_data_micros=900_000_000, spend_sending_micros=400_000_000,
                  spend_people_micros=12_000_000_000)
    fields.update(overrides)
    return PeriodSpend(**fields)


def _report(**overrides) -> IncrementalityReport:
    fields = dict(
        program_key="flagship", program_version="2.1.0", spec_hash="abc",
        period_start=WINDOW["window_start"], period_end=WINDOW["window_end"],
        holdout_pct=10.0,
        primary=Comparison(1000, 1000, 200, 100),
        opportunities=Comparison(1000, 1000, 0, 0),
        converted_by_type={"meeting": {"treatment": 100, "control": 50}},
        credits_by_kind={"email.send": 1_200_000.0}, baseline=BASELINE)
    fields.update(overrides)
    return IncrementalityReport(**fields)


# -- the declaration ------------------------------------------------------

def test_the_total_is_the_four_fields_and_the_vocabulary_is_the_baseline_s():
    """Two lists that must agree and are written twice are two lists that will
    disagree. The whole point of decision 46 is that the before and the after
    are one measurement."""
    import zolts.spend

    assert zolts.spend.SPEND_FIELDS is SPEND_FIELDS, "the fields are shared, not copied"
    assert _spend().total_micros == 14_700_000_000
    assert _spend().days == 28


def test_a_declared_zero_is_a_declaration_and_not_an_absence():
    """A tenant who ran the period on Zolts alone means the four zeros they
    wrote, and the report must divide by that rather than reaching for a
    run-rate they have replaced."""
    nothing = _spend(**{name: 0 for name in SPEND_FIELDS})
    assert nothing.total_micros == 0
    assert _report(period_spend_micros=nothing.total_micros).own_spend_basis == DECLARED


@pytest.mark.parametrize("bad, message", [
    ({"window_start": date(2026, 10, 1), "window_end": date(2026, 9, 3)}, "ends before"),
    ({"spend_people_micros": -1}, "negative"),
    ({"spend_tools_micros": 1.5}, "whole number"),
])
def test_a_declaration_that_cannot_be_recorded_is_refused_with_the_reason(bad, message):
    with pytest.raises(SpendError, match=message):
        _spend(**bad)


def test_the_declaration_round_trips_through_its_own_digest():
    original = _spend()
    assert spend_from_mapping(original.canonical()).digest() == original.digest()
    assert spend_from_mapping(original.canonical()) == original


def test_a_declaration_missing_a_field_names_the_field():
    body = _spend().canonical()
    del body["spend_data_micros"]
    with pytest.raises(SpendError, match="spend_data_micros"):
        spend_from_mapping(body)


def test_moving_one_euro_moves_the_digest():
    """The row a report divided by and the figure an operator declared are
    checked against each other by this hash."""
    assert _spend().digest() != _spend(spend_tools_micros=1_400_000_001).digest()


# -- which basis the report used ------------------------------------------

def test_a_declaration_is_preferred_over_the_prorated_run_rate():
    run = _report()
    declared = _report(period_spend_micros=14_000_000_000)

    assert run.own_spend_basis == RUN_RATE
    assert run.own_spend_micros == RUN_RATE_MICROS
    assert declared.own_spend_basis == DECLARED
    assert declared.own_spend_micros == 14_000_000_000

    assert declared.cost_per_incremental_meeting_micros == 520_000_000
    assert run.cost_per_incremental_meeting_micros == 447_905_706
    assert declared.cost_per_incremental_meeting_micros != \
        run.cost_per_incremental_meeting_micros, (
            "if the two bases gave the same figure this work would be decoration")


def test_a_declaration_is_never_prorated():
    """Prorating it would put back the assumption it exists to remove. The
    figures are for this period, whatever length the period is."""
    short = _report(period_end=date(2026, 9, 10), period_spend_micros=14_000_000_000)
    assert short.period_days == 7
    assert short.own_spend_micros == 14_000_000_000


def test_a_declaration_carries_a_report_that_has_no_baseline_at_all():
    """The two sources are independent: a tenant who never froze a baseline can
    still be measured on a period they declared."""
    orphan = _report(baseline=None, period_spend_micros=14_000_000_000)
    assert orphan.own_spend_basis == DECLARED
    assert orphan.cost_per_incremental_meeting_micros == 520_000_000


def test_neither_source_withholds_the_figure_and_names_both():
    bare = _report(baseline=None)
    assert bare.own_spend_basis is None
    assert bare.own_spend_micros is None
    assert bare.cost_per_incremental_meeting_micros is None
    assert "neither a declaration for this period nor a baseline" in \
        bare.cost_per_meeting_withheld_because


def test_the_document_disclaims_an_assumption_and_does_not_disclaim_a_measurement():
    """The reason the basis is on the page at all."""
    run = _report().render_markdown()
    assert "an assumption, because nothing re-measures it" in run
    assert "declared for this period" not in run

    declared = _report(period_spend_micros=14_000_000_000).render_markdown()
    assert "declared for this period" in declared
    assert "an assumption" not in declared


# -- the version ----------------------------------------------------------

def test_the_basis_is_covered_by_the_digest():
    """Two reports with the same figure and different bases are two different
    claims, and a signature has to stand behind which."""
    run = _report()
    declared = _report(period_spend_micros=run.own_spend_micros)
    assert declared.own_spend_micros == run.own_spend_micros, "the same euros"
    assert declared.digest() != run.digest(), "and not the same claim"

    canonical = declared.canonical()
    for key in ("period_spend_micros", "own_spend_micros", "own_spend_basis"):
        assert key in canonical


def test_the_new_keys_belong_to_version_three_and_the_older_shapes_are_intact():
    assert SCHEMA_VERSION == 3
    assert set(KEYS_ADDED_IN[3]) == {
        "period_spend_micros", "own_spend_micros", "own_spend_basis"}
    older = _report(schema_version=2).canonical()
    for key in KEYS_ADDED_IN[3]:
        assert key not in older
    assert older["schema_version"] == 2


def test_a_version_three_report_round_trips_and_keeps_its_basis():
    declared = _report(period_spend_micros=14_000_000_000)
    rebuilt = from_mapping(declared.canonical())
    assert rebuilt.period_spend_micros == 14_000_000_000
    assert rebuilt.own_spend_basis == DECLARED
    assert rebuilt.digest() == declared.digest()
    assert rebuilt.render_markdown() == declared.render_markdown()


def test_a_declared_zero_survives_the_round_trip_as_a_declaration():
    """`or None` on the way back would turn a tenant who spent nothing outside
    Zolts into one who never told us, and silently restore the run-rate."""
    rebuilt = from_mapping(_report(period_spend_micros=0).canonical())
    assert rebuilt.period_spend_micros == 0
    assert rebuilt.own_spend_basis == DECLARED
    assert rebuilt.own_spend_micros == 0


# -- what the runtime stores and refuses ----------------------------------

def _tenant_row(cur, tid: str) -> dict:
    cur.execute("select * from tenant where id = %s", (tid,))
    return dict(cur.fetchone())


def _program_with_meetings(cur, tid: str, *, booked=(100, 50), enrolled=1000) -> str:
    """A programme whose meeting comparison resolves, so a cost per meeting
    exists to divide the declaration by."""
    import json

    cur.execute(
        "insert into program (tenant_id, key, version, spec, spec_hash, status)"
        " values (%s,'declared','1.0.0',%s,'h-declared','live') returning id",
        (tid, json.dumps({"experiment": {"holdout_pct": 10}})))
    program_id = str(cur.fetchone()["id"])
    for variant, meetings in (("treatment", booked[0]), ("control", booked[1])):
        for i in range(enrolled):
            cur.execute(
                "insert into enrollment (tenant_id, program_id, entity_type, entity_id,"
                " variant, state, entered_at) values (%s,%s,'account',gen_random_uuid(),"
                " %s,'running', now()) returning id", (tid, program_id, variant))
            enrollment_id = str(cur.fetchone()["id"])
            if i < meetings:
                cur.execute(
                    "insert into outcome (tenant_id, enrollment_id, type, occurred_at,"
                    " source) values (%s,%s,'meeting',now(),'t')", (tid, enrollment_id))
    return program_id


@requires_db
def test_a_period_s_spend_is_declared_once_and_never_replaced(db, tenant):
    """ADR-042's rule, applied to the third document a signed report rests on:
    a figure that can be corrected after the result is read is one that will
    be."""
    from runtime import metering
    from runtime.repo import period_spend as repo

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        period = metering.open_period(cur, _tenant_row(cur, tid))
        row = repo.declare(cur, tid, str(period["id"]), _spend(), declared_by="operator")
        assert int(row["total_micros"]) == 14_700_000_000
        assert row["digest"] == _spend().digest()

        with pytest.raises(repo.SpendAlreadyDeclared, match="never replaced"):
            repo.declare(cur, tid, str(period["id"]),
                         _spend(spend_people_micros=1), declared_by="operator")

        assert int(repo.get(cur, str(period["id"]))["total_micros"]) == 14_700_000_000


@requires_db
def test_a_declaration_after_the_reports_are_frozen_is_refused(db, tenant):
    """Not a duplicate and not a typo: the operator has missed the window, and
    a row written now would disagree with every document of that period while
    changing none of them."""
    from runtime import metering, reporting
    from runtime.repo import period_spend as repo

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _program_with_meetings(cur, tid, booked=(3, 2), enrolled=10)
        row = _tenant_row(cur, tid)
        period = metering.open_period(cur, row)
        closed = metering.close_period(cur, row, str(period["id"]))
        reporting.freeze_all(cur, row, closed, frozen_by="operator")

        with pytest.raises(repo.ReportAlreadyFrozen, match="frozen"):
            repo.declare(cur, tid, str(period["id"]), _spend(), declared_by="operator")


@requires_db
def test_the_frozen_report_divides_by_the_declaration_and_says_it_did(db, tenant):
    """The whole chain: an operator declares, the period closes, and the signed
    document names the basis rather than leaving a reader to assume one."""
    from runtime import metering, reporting
    from runtime.repo import baseline as baseline_repo
    from runtime.repo import period_spend as repo
    from tests.test_baseline import DECLARED as DECLARED_BASELINE
    from zolts.baseline import from_mapping as baseline_from_mapping

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _program_with_meetings(cur, tid, booked=(90, 20), enrolled=400)
        row = _tenant_row(cur, tid)
        baseline_repo.freeze(cur, tid, baseline_from_mapping(DECLARED_BASELINE),
                             signed_by="R. Ortega", captured_by="operator")
        period = metering.open_period(cur, row)
        repo.declare(cur, tid, str(period["id"]), _spend(), declared_by="operator")
        closed = metering.close_period(cur, row, str(period["id"]))
        [frozen] = reporting.freeze_all(cur, row, closed, frozen_by="operator")

    body = frozen["body"]
    assert body["own_spend_basis"] == DECLARED
    assert body["period_spend_micros"] == 14_700_000_000
    assert body["own_spend_micros"] == 14_700_000_000, "declared, and not prorated"
    assert body["acquisition_spend_micros"] == 14_700_000_000 + round(
        body["credits_total"] * 10_000)
    assert "declared for this period" in frozen["rendered"]
    assert "an assumption" not in frozen["rendered"]


@requires_db
def test_a_period_nobody_declared_falls_back_and_says_so(db, tenant):
    """The fallback is not removed, because a month's close must not wait on
    data entry (decision 46)."""
    from runtime import metering, reporting
    from runtime.repo import baseline as baseline_repo
    from tests.test_baseline import DECLARED as DECLARED_BASELINE
    from zolts.baseline import from_mapping as baseline_from_mapping

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _program_with_meetings(cur, tid, booked=(90, 20), enrolled=400)
        row = _tenant_row(cur, tid)
        baseline_repo.freeze(cur, tid, baseline_from_mapping(DECLARED_BASELINE),
                             signed_by="R. Ortega", captured_by="operator")
        period = metering.open_period(cur, row)
        closed = metering.close_period(cur, row, str(period["id"]))
        [frozen] = reporting.freeze_all(cur, row, closed, frozen_by="operator")

    assert frozen["body"]["own_spend_basis"] == RUN_RATE
    assert frozen["body"]["period_spend_micros"] is None
    assert "an assumption, because nothing re-measures it" in frozen["rendered"]


@requires_app_role
def test_the_serving_role_cannot_restate_a_declaration(db, tenant, app_role_is_restricted):
    """Written once by construction and not only by convention, like the two
    documents beside it (ADR-043)."""
    import psycopg

    from runtime import metering
    from runtime.repo import period_spend as repo

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        period = metering.open_period(cur, _tenant_row(cur, tid))
        repo.declare(cur, tid, str(period["id"]), _spend(), declared_by="operator")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("update tenant_period_spend set total_micros = 1")


# -- what the documents claim ---------------------------------------------

def test_the_documents_record_the_basis_and_the_rule():
    """A claim in `docs/` that can be tested has a test, and the rules that
    constrain future work are in an ADR rather than in this file's memory."""
    from pathlib import Path

    docs = Path(__file__).resolve().parents[1] / "docs"
    architecture = (docs / "02-architecture.md").read_text()
    assert "ADR-046" in architecture
    assert "A declaration is never prorated" in architecture
    assert "the serving role cannot restate it" in architecture

    register = (docs / "18-decision-register.md").read_text()
    forty_six = next(ln for ln in register.splitlines() if ln.startswith("| 46 |"))
    assert "The follow-on is built" in forty_six
    assert "own_spend_basis" in forty_six

    measurement = (docs / "10-measurement-and-incrementality.md").read_text()
    assert "zolts period-spend" in measurement
    assert "ADR-046" in measurement


def test_the_command_line_offers_the_declaration_and_says_when_it_is_too_late():
    """A surface nobody can find is a feature nobody has."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "runtime" / "cli.py").read_text()
    assert '"period-spend"' in source
    assert "--period" in source
    assert "Declare before the period closes" in source



# -- the premise those isolation tests rest on ----------------------------

def test_a_privilege_test_skips_rather_than_fails_without_the_role_it_needs():
    """D-73. Nine tests assert what the *restricted* role cannot do — tenant
    isolation among them. With no separate app URL the pool connects as the
    owner, every one of those assertions fails, and the failure is about the
    environment rather than the product: red on a clean checkout, on code that
    was never wrong. They now skip with a reason that names the variable.
    """
    from pathlib import Path

    conftest = (Path(__file__).resolve().parents[1] / "tests" / "conftest.py").read_text()
    assert "def requires_app_role" in conftest
    assert "ZOLTS_TEST_APP_DATABASE_URL is not set" in conftest

    # And skipping is not allowed to be how the verification disappears.
    workflow = (Path(__file__).resolve().parents[1]
                / ".github" / "workflows" / "validate.yml").read_text()
    assert 'grep -q "ZOLTS_TEST_APP_DATABASE_URL is not set"' in workflow, (
        "CI must refuse a green run that skipped the isolation tests")


def test_the_premise_is_asserted_and_not_merely_declared():
    """A URL that points at the owner by mistake would satisfy the skipif and
    prove nothing, so the fixture checks the connection rather than the
    variable."""
    from pathlib import Path

    conftest = (Path(__file__).resolve().parents[1] / "tests" / "conftest.py").read_text()
    assert "def app_role_is_restricted" in conftest
    assert "db.isolation_enforced" in conftest
    assert "select current_user" in conftest


@requires_db
def test_the_runbook_s_query_runs_and_its_keys_resolve(db, tenant):
    """`docs/26` tells an operator to read the basis with a SQL query. A query
    in a document nobody executes is the shape this register carries most of,
    and the report body's keys have changed twice this month — so the query is
    taken from the document itself and run against a real frozen report.
    """
    import re
    from pathlib import Path

    from runtime import metering, reporting
    from runtime.repo import period_spend as repo

    runbook = (Path(__file__).resolve().parents[1] / "docs"
               / "26-partner-onboarding.md").read_text()
    [sql] = [block for block in re.findall(r"```sql\n(.*?)```", runbook, re.S)
             if "own_spend_basis" in block]

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _program_with_meetings(cur, tid, booked=(90, 20), enrolled=400)
        row = _tenant_row(cur, tid)
        period = metering.open_period(cur, row)
        repo.declare(cur, tid, str(period["id"]), _spend(), declared_by="operator")
        closed = metering.close_period(cur, row, str(period["id"]))
        reporting.freeze_all(cur, row, closed, frozen_by="operator")

        cur.execute(sql.replace("limit 3", "").rstrip().rstrip(";"))
        [read] = cur.fetchall()

    assert read["basis"] == DECLARED, "the query reads the key the report writes"
    assert read["own_spend_eur"] == 14_700, "and the figure the operator declared"
    assert read["per_meeting_eur"] is not None
