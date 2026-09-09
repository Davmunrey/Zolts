"""A guardrail is a metric the holdout can also exhibit, or it is not a guardrail.

`spec.experiment.guardrail_metrics` was declared by all four shipped archetypes
and read by nothing: `zolts.controls` carried it as the sharpest of eleven
unenforced controls, because a programme that wins on its primary metric while
damaging a guardrail reported an unqualified win.

Wiring it up turned out to be the smaller half. Of the six names the archetypes
declared, none existed in the metric registry, and four of them could never
exist there. Unsubscribes and complaints are stamped on the *touch*
(`runtime/engine/inbound.py`, `unsubscribed_at` and `complained_at`), and the
holdout is never touched — `runtime/engine/planner.py` stops on
`variant == "control"` as product invariant 4. So a comparison of an
unsubscribe rate against a holdout has a control arm that is structurally zero,
is never resolvable, and would report *not resolvable* for ever while reading
like a measurement that has not gathered enough data yet (D-76).

They are real limits. They are already enforced, per mailbox and per domain, by
the deliverability rules ADR-020 sets. What they are not is a question a holdout
can be asked, and the tests below fix both halves of that: the refusal at
admission, and the measurement of what survives it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from runtime.engine import admission
from tests.conftest import requires_db
from zolts import controls, metrics
from zolts.report import (DEGRADED, GUARDRAIL_VERDICTS, HELD, NOT_RESOLVABLE,
                          SCHEMA_VERSION, Comparison, Guardrail,
                          IncrementalityReport, from_mapping)

PROGRAMS = sorted(Path("examples/programs").glob("*.yaml"))


# -- the contract in `zolts.metrics` ------------------------------------------

@pytest.mark.parametrize("name", sorted(metrics.UNCOMPARABLE_TO_A_HOLDOUT))
def test_a_touch_derived_metric_is_refused_rather_than_measured_to_nothing(name):
    """The defect, stated directly. Measuring one of these is not wrong in the
    sense of returning a wrong number — it returns *no* number, for ever, and
    a reader cannot tell that from a programme that is still warming up."""
    with pytest.raises(metrics.MetricError) as caught:
        metrics.resolve_guardrail(name)
    message = str(caught.value)
    assert "holdout" in message, "the refusal must say why, not merely refuse"
    assert "ADR-020" in message, "and where the limit really is enforced"


@pytest.mark.parametrize("name", sorted(metrics.UNCOMPARABLE_TO_A_HOLDOUT))
def test_the_refused_names_are_refused_because_of_the_holdout_and_not_by_a_list(name):
    """Establishes the premise the refusal rests on rather than trusting the
    dictionary that encodes it: each of these names is genuinely absent from
    the registry, so nothing would have measured it anyway, and the message is
    the only thing standing between a customer and a silent nothing."""
    assert name not in metrics.METRICS, (
        f"{name} is refused as uncomparable and also registered as measurable; "
        "one of the two is a lie")


def test_a_metric_the_runtime_cannot_count_is_refused():
    with pytest.raises(metrics.MetricError, match="not a metric this runtime can measure"):
        metrics.resolve_guardrail("margin_per_order")


def test_a_value_metric_cannot_be_a_guardrail():
    """A value metric's count is tested and its amount is explicitly not
    (`zolts.metrics`). A guardrail on the amount therefore has no verdict to
    report, which is a guard that passes because it can never fire."""
    assert metrics.METRICS["net_revenue_28d"].kind == metrics.VALUE, (
        "the premise: this test is about a value metric")
    with pytest.raises(metrics.MetricError, match="value metric"):
        metrics.resolve_guardrail("net_revenue_28d")


def test_a_rate_metric_resolves_to_itself():
    metric = metrics.resolve_guardrail("signed_contract_60d")
    assert metric.kind == metrics.RATE
    assert metric.window_days == 60
    assert metric.events == ("won",)


# -- the refusal at the choke point -------------------------------------------

def _spec(guardrails: list[str], *, primary: str = "opportunity_created_90d") -> dict:
    return {
        "trigger": {"events": [{"signal": "funding.round"}], "window": "30d"},
        "audience": {"sql": "select a.id as account_id from account a"},
        "score": {"floor": 0},
        "route": {"tiers": [{"key": "t1"}]},
        "plays": {"t1": {}},
        "experiment": {"holdout_pct": 10, "unit": "account",
                       "primary_metric": primary, "guardrail_metrics": guardrails},
    }


def test_a_programme_declaring_a_measurable_guardrail_is_admissible():
    admission.check(_spec(["signed_contract_60d"]), "candidate")


def test_a_programme_declaring_no_guardrail_is_admissible():
    """Empty is a real answer and the commonest one. Three of the four shipped
    archetypes give it, because the guardrails they want are not outcomes this
    runtime records."""
    admission.check(_spec([]), "candidate")
    spec = _spec([])
    spec["experiment"].pop("guardrail_metrics")
    admission.check(spec, "candidate")


@pytest.mark.parametrize("name", sorted(metrics.UNCOMPARABLE_TO_A_HOLDOUT))
def test_admission_refuses_a_guardrail_the_holdout_cannot_exhibit(name):
    """The same 422 the primary metric already got. `metrics.MetricError` was
    already in admission's except tuple, so the refusal needed no new failure
    type — which is the point of having one type at the boundary."""
    with pytest.raises(admission.NotAdmissible, match="holdout"):
        admission.check(_spec([name]), "candidate")


def test_admission_refuses_a_guardrail_that_is_the_primary_metric():
    """It cannot disagree with itself, so it can never fire."""
    with pytest.raises(admission.NotAdmissible, match="already this programme's primary"):
        admission.check(_spec(["opportunity_created_90d"]), "candidate")


def test_admission_refuses_the_same_guardrail_twice():
    with pytest.raises(admission.NotAdmissible, match="declared twice"):
        admission.check(_spec(["signed_contract_60d", "signed_contract_60d"]),
                        "candidate")


# -- what the shipped archetypes declare --------------------------------------

@pytest.mark.parametrize("path", PROGRAMS, ids=lambda p: p.stem)
def test_every_shipped_programme_is_admissible_on_its_guardrails(path):
    """The content half of the fix. All four declared names the runtime cannot
    measure, and two of them declared names it never could."""
    spec = yaml.safe_load(path.read_text())["spec"]
    admission.check(spec, path.stem)


@pytest.mark.parametrize("path", PROGRAMS, ids=lambda p: p.stem)
def test_a_shipped_programme_says_where_its_guardrails_went(path):
    """A key removed leaves no trace; a key present and empty is a statement.
    The archetypes are read as documentation, so the absence has to be
    deliberate on the page rather than deducible from the schema."""
    text = path.read_text()
    assert "guardrail_metrics:" in text, (
        "a shipped archetype that simply drops the key teaches the reader the "
        "field does not exist")
    assert "D-76" in text, "and the empty list has to say why it is empty"


def test_at_least_one_shipped_programme_exercises_a_guardrail():
    """Otherwise the four archetypes document the refusal and never the
    measurement, and the feature ships with no worked example."""
    declared = [name
                for path in PROGRAMS
                for name in (yaml.safe_load(path.read_text())["spec"]
                             .get("experiment", {}).get("guardrail_metrics") or [])]
    assert declared, "no shipped programme declares a guardrail at all"
    for name in declared:
        metrics.resolve_guardrail(name)


# -- the verdict --------------------------------------------------------------

def test_a_guardrail_verdict_is_not_the_primary_metrics_verdict():
    """`Comparison.verdict` tests `lift > mde`, so *significant* on the primary
    metric can only ever mean the treatment arm did better. Reusing the word
    for a guardrail would tell a reader the opposite of what happened."""
    harmed = Comparison(1000, 1000, 50, 100)
    assert harmed.lift == pytest.approx(-0.05), "the premise: the treatment arm did worse"
    assert harmed.verdict == "not significant", (
        "the primary metric's vocabulary has no word for a measurable loss")
    assert Guardrail("signed_contract_60d", 60, harmed).verdict == DEGRADED


def test_a_drop_inside_the_detectable_effect_is_not_a_breach():
    """Symmetric with the primary metric's own test. A guardrail that fires on
    noise is one an operator learns to ignore."""
    small = Comparison(1000, 1000, 95, 100)
    assert small.lift == pytest.approx(-0.005)
    assert small.minimum_detectable_effect == pytest.approx(0.037587, abs=1e-6), (
        "the premise: the drop is well inside what these arms can detect")
    assert Guardrail("signed_contract_60d", 60, small).verdict == HELD


def test_an_improvement_on_a_guardrail_is_held_and_never_a_breach():
    better = Comparison(1000, 1000, 100, 50)
    assert better.lift == pytest.approx(0.05)
    assert not Guardrail("signed_contract_60d", 60, better).breached


def test_arms_too_small_to_say_are_reported_as_such():
    tiny = Comparison(1000, 1000, 3, 4)
    assert not tiny.resolvable, "the premise: fewer than five conversions in an arm"
    assert Guardrail("signed_contract_60d", 60, tiny).verdict == NOT_RESOLVABLE


def test_the_guardrail_vocabulary_is_exactly_three_words():
    seen = {Guardrail("signed_contract_60d", 60, c).verdict
            for c in (Comparison(1000, 1000, 50, 100), Comparison(1000, 1000, 100, 50),
                      Comparison(1000, 1000, 3, 4))}
    assert seen == set(GUARDRAIL_VERDICTS)


# -- the report ---------------------------------------------------------------

def _report(guardrails: tuple[Guardrail, ...] = (), **overrides) -> IncrementalityReport:
    fields = dict(
        program_key="flagship", program_version="2.1.0", spec_hash="abc",
        period_start=date(2026, 5, 1), period_end=date(2026, 6, 1), holdout_pct=10.0,
        primary=Comparison(1000, 1000, 90, 40),
        opportunities=Comparison(1000, 1000, 30, 10),
        guardrails=guardrails)
    fields.update(overrides)
    return IncrementalityReport(**fields)


BREACHED = Guardrail("signed_contract_60d", 60, Comparison(1000, 1000, 50, 100))
INTACT = Guardrail("signed_contract_60d", 60, Comparison(1000, 1000, 100, 50))


def test_a_won_programme_that_damaged_a_guardrail_does_not_read_as_an_unqualified_win():
    """The consequence `zolts.controls` recorded, stated as the reader sees it.
    The qualification sits in the verdict paragraph, not in a section further
    down, because a reader who stops after the first paragraph is the reader
    it is for."""
    report = _report((BREACHED,))
    assert report.verdict == "significant", "the premise: the programme won its primary metric"
    verdict_paragraph = report.render_markdown().split("## Arms")[0]
    assert "did not come free" in verdict_paragraph
    assert "signed_contract_60d" in verdict_paragraph


def test_an_intact_guardrail_adds_nothing_to_the_verdict_paragraph():
    report = _report((INTACT,))
    assert report.guardrail_qualification is None
    assert "did not come free" not in report.render_markdown()


def test_a_report_with_no_guardrails_says_so_rather_than_showing_nothing():
    """A blank section reads as a report that forgot to check."""
    assert "None declared" in _report().render_markdown()


def test_the_guardrail_table_carries_the_window_it_was_measured_in():
    rendered = _report((INTACT,)).render_markdown()
    assert "| `signed_contract_60d` | 60d |" in rendered


def test_guardrails_round_trip_through_the_canonical_form():
    """The stronger of `docs/10`'s two checks: rebuild the document from the
    JSON and recompute the digest. A key that does not survive the round trip
    is a report a counterparty cannot verify (D-72)."""
    report = _report((BREACHED,))
    body = report.canonical()
    assert body["schema_version"] == SCHEMA_VERSION
    rebuilt = from_mapping(body)
    assert rebuilt.guardrails == report.guardrails
    assert rebuilt.digest() == report.digest()
    assert rebuilt.render_markdown() == report.render_markdown()


def test_a_guardrail_changes_the_digest():
    """Otherwise the guardrails are decoration beside the signature rather than
    part of the claim it covers."""
    assert _report((BREACHED,)).digest() != _report((INTACT,)).digest()
    assert _report((INTACT,)).digest() != _report().digest()


def test_an_older_document_re_renders_without_the_section_it_never_had():
    """Re-rendering a signed letter has to produce the letter that was signed."""
    older = _report((BREACHED,), schema_version=3)
    assert "guardrails" not in older.canonical()
    assert "## Guardrails" not in older.render_markdown()
    assert "did not come free" in older.render_markdown(), (
        "the verdict paragraph is not versioned; only the section is")


def test_a_declared_guardrail_the_runtime_cannot_measure_is_named_not_dropped():
    """Admission refuses these now, so only a programme published before D-76
    can hold one. A report that silently omits it is the original defect
    arriving one layer later."""
    report = _report(guardrails_not_measured=(
        ("unsubscribe_rate", "the holdout is never touched"),))
    rendered = report.render_markdown()
    assert "None measured" in rendered
    assert "unsubscribe_rate" in rendered
    assert "the holdout is never touched" in rendered
    assert from_mapping(report.canonical()).digest() == report.digest()


# -- the register -------------------------------------------------------------

def test_the_control_register_says_reported_and_not_enforced():
    """A guardrail pauses nothing, exactly like the cost ceiling (decision 45).
    Recording it as enforced would be the confusion `zolts.controls` exists to
    prevent."""
    control = controls.BY_PATH["spec.experiment.guardrail_metrics"]
    assert not control.honoured, "nothing holds a guardrail; the report states it"
    assert control.reported, "and it is no longer invisible"
    assert "zolts/report.py" in (control.reported_by or "")


# -- what the runtime measures ------------------------------------------------

GUARDED_SPEC = {"experiment": {"holdout_pct": 10, "unit": "account",
                               "primary_metric": "opportunity_created_90d",
                               "guardrail_metrics": ["signed_contract_60d"]}}
# The shape only a programme published before D-76 can have. Written straight
# into the table, because admission refuses it now — which the test asserts
# before it relies on it.
LEGACY_SPEC = {"experiment": {"holdout_pct": 10, "unit": "account",
                              "primary_metric": "opportunity_created_90d",
                              "guardrail_metrics": ["unsubscribe_rate"]}}


def _guarded_program(cur, tenant_id: str, spec: dict, key: str = "guarded") -> dict:
    cur.execute(
        "insert into program (tenant_id, key, version, spec, spec_hash, status)"
        " values (%s,%s,'1.0.0',%s,'h','live') returning *",
        (tenant_id, key, json.dumps(spec)))
    return dict(cur.fetchone())


def _arm(cur, tenant_id: str, program_id: str, variant: str, n: int, *,
         won: int = 0) -> None:
    for i in range(n):
        cur.execute(
            "insert into enrollment (tenant_id, program_id, entity_type, entity_id,"
            " variant, state, entered_at) values (%s,%s,'account',gen_random_uuid(),"
            " %s,'running',now()) returning id", (tenant_id, program_id, variant))
        enrollment_id = str(cur.fetchone()["id"])
        if i < won:
            cur.execute(
                "insert into outcome (tenant_id, enrollment_id, type, occurred_at, source)"
                " values (%s,%s,'won',now(),'crm')", (tenant_id, enrollment_id))


def _period(cur, tenant_id: str) -> dict:
    from runtime import metering

    cur.execute("select * from tenant where id = %s", (tenant_id,))
    row = dict(cur.fetchone())
    period = metering.open_period(cur, row)
    return metering.close_period(cur, row, str(period["id"]))


@requires_db
def test_a_declared_guardrail_is_measured_against_the_same_holdout(db, tenant):
    """The whole point: a guardrail is not a second kind of measurement. Same
    arms, same concurrent control, the guardrail's own events inside the
    guardrail's own window."""
    from runtime import reporting

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        program = _guarded_program(cur, tid, GUARDED_SPEC)
        _arm(cur, tid, str(program["id"]), "treatment", 6, won=2)
        _arm(cur, tid, str(program["id"]), "control", 4, won=3)
        report = reporting.compose(cur, program, _period(cur, tid))

    assert len(report.guardrails) == 1
    guardrail = report.guardrails[0]
    assert guardrail.name == "signed_contract_60d"
    assert guardrail.window_days == 60
    assert guardrail.comparison.treatment_enrolled == 6
    assert guardrail.comparison.control_enrolled == 4
    assert guardrail.comparison.treatment_converted == 2
    assert guardrail.comparison.control_converted == 3
    assert not report.guardrails_not_measured


@requires_db
def test_a_guardrail_counts_its_own_events_and_not_the_primary_metrics(db, tenant):
    """`opportunity_created_90d` counts `opp_created` and `signed_contract_60d`
    counts `won`. A guardrail reading the primary metric's events would agree
    with it by construction and never fire."""
    from runtime import reporting

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        program = _guarded_program(cur, tid, GUARDED_SPEC)
        _arm(cur, tid, str(program["id"]), "treatment", 6)
        _arm(cur, tid, str(program["id"]), "control", 4)
        cur.execute("select id from enrollment where program_id = %s limit 3",
                    (str(program["id"]),))
        for row in cur.fetchall():
            cur.execute(
                "insert into outcome (tenant_id, enrollment_id, type, occurred_at, source)"
                " values (%s,%s,'opp_created',now(),'crm')", (tid, str(row["id"])))
        report = reporting.compose(cur, program, _period(cur, tid))

    assert report.primary.treatment_converted + report.primary.control_converted == 3, (
        "the premise: three opportunities exist and no deals were won")
    guardrail = report.guardrails[0].comparison
    assert guardrail.treatment_converted == 0 and guardrail.control_converted == 0


@requires_db
def test_a_programme_published_before_the_refusal_does_not_break_the_close(db, tenant):
    """A freeze that raised on one stale programme would stop the whole
    tenant's period close. The name is dropped from the measurement and carried
    into the document with its reason, because a declared control that vanishes
    from the report is the original defect one layer later."""
    from runtime import reporting

    with pytest.raises(admission.NotAdmissible):
        admission.check(LEGACY_SPEC, "legacy")

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        program = _guarded_program(cur, tid, LEGACY_SPEC, key="legacy")
        _arm(cur, tid, str(program["id"]), "treatment", 6, won=2)
        _arm(cur, tid, str(program["id"]), "control", 4, won=1)
        report = reporting.compose(cur, program, _period(cur, tid))

    assert report.guardrails == ()
    assert [name for name, _ in report.guardrails_not_measured] == ["unsubscribe_rate"]
    assert "holdout" in report.guardrails_not_measured[0][1]
    assert "unsubscribe_rate" in report.render_markdown()
