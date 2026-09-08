"""A program is measured on the metric it declares, inside the window it declares.

`examples/schema/zolts-program.schema.json` requires `experiment.primary_metric`,
the console printed it on the programme's own panel, and nothing measured by
it (D-51). Four shipped programmes declare four different metrics and all four
were counted the same way — any of `opp_created`, `meeting` or
`reply_positive`, at any time after enrolment. So the PLG programme that says
it measures paid conversions was judged on positive replies, and a ninety-day
metric counted a conversion from month six.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from tests.conftest import requires_db
from zolts import metrics
from zolts.catalog import load_catalog


# -- the registry ----------------------------------------------------------

def test_every_shipped_program_declares_a_metric_this_runtime_can_measure():
    """The join that was missing. A programme naming a metric nothing counts
    is a programme measured on something else without saying so."""
    for program in load_catalog().programs:
        declared = program.spec["experiment"]["primary_metric"]
        metric = metrics.resolve(declared)
        assert metric.name == declared
        assert metric.events, f"{program.key} resolves to a metric that counts nothing"
        assert metric.window_days > 0


def test_a_metric_nothing_can_count_is_refused_rather_than_defaulted():
    """A silent fallback is this defect wearing a default: the programme would
    go on being measured on something other than what it declares."""
    with pytest.raises(metrics.MetricError, match="not a metric"):
        metrics.resolve("revenue_uplift_14d")
    with pytest.raises(metrics.MetricError, match="not a metric"):
        metrics.resolve("account_activated")


def test_a_metric_with_no_window_cannot_be_registered():
    """Without a window a treatment arm enrolled in January is compared with a
    control arm still accumulating in June."""
    with pytest.raises(metrics.MetricError, match="names no window"):
        metrics._metric("signed_contract", ("won",), metrics.RATE, "")


def test_the_default_is_what_the_runtime_counted_before_metrics_existed():
    """Kept, and named, so a spec written before the registry keeps the
    meaning it had rather than silently changing on a deploy."""
    default = metrics.resolve(None)
    assert set(default.events) == {"opp_created", "meeting", "reply_positive"}
    assert default.window_days == 90


def test_a_value_metric_counts_its_event_and_never_tests_the_amount():
    """`net_revenue_28d` is a value metric. The two-proportion test this
    product reports asks whether a larger share converted; a mean-difference
    test on revenue is different statistics and is not claimed here."""
    revenue = metrics.resolve("net_revenue_28d")
    assert revenue.kind == metrics.VALUE
    assert revenue.events == ("won",)
    assert revenue.tests_value is False
    assert metrics.resolve("signed_contract_60d").kind == metrics.RATE


# -- what the measurement now counts ---------------------------------------

def _program(cur, tid: str, key: str, metric: str) -> str:
    import json

    spec = {"experiment": {"holdout_pct": 10, "primary_metric": metric}}
    cur.execute(
        "insert into program (tenant_id, key, version, spec, spec_hash, status)"
        " values (%s,%s,'1.0.0',%s,%s,'live') returning id",
        (tid, key, json.dumps(spec), f"h-{key}"))
    return str(cur.fetchone()["id"])


def _enrol(cur, tid: str, program_id: str, variant: str, *, outcome: str | None = None,
           days_after: int = 1) -> str:
    cur.execute(
        "insert into enrollment (tenant_id, program_id, entity_type, entity_id, variant,"
        " state, entered_at) values (%s,%s,'account',gen_random_uuid(),%s,'running',"
        " now() - interval '200 days') returning id",
        (tid, program_id, variant))
    enrollment_id = str(cur.fetchone()["id"])
    if outcome:
        cur.execute(
            "insert into outcome (tenant_id, enrollment_id, type, occurred_at, source,"
            " dedupe_key) values (%s,%s,%s, now() - interval '200 days' + %s, 'test', %s)",
            (tid, enrollment_id, outcome, timedelta(days=days_after),
             f"k-{uuid.uuid4().hex}"))
    return enrollment_id


@requires_db
def test_a_program_measured_on_contracts_does_not_count_replies(db, tenant):
    """The defect, stated as the two programmes it applied to. Both arms are
    identical apart from what they produced."""
    from runtime.api import console

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        contracts = _program(cur, tid, "contracts", "signed_contract_60d")
        replies = _program(cur, tid, "replies", "positive_reply_14d")
        for program_id in (contracts, replies):
            for _ in range(8):
                _enrol(cur, tid, program_id, "treatment", outcome="reply_positive")
            for _ in range(8):
                _enrol(cur, tid, program_id, "control")
        # One real contract, in the treatment arm of the contracts programme.
        _enrol(cur, tid, contracts, "treatment", outcome="won")

        cur.execute("select * from tenant where id = %s", (tid,))
        view = console.build(cur, dict(cur.fetchone()))

    by_key = {p["key"]: p for p in view["programs"]}
    assert by_key["replies"]["conversions"] == 8, "positive replies are what it measures"
    assert by_key["contracts"]["conversions"] == 1, (
        "the contracts programme counted its replies as conversions")
    assert by_key["contracts"]["metricWindowDays"] == 60
    assert "won" in by_key["contracts"]["metricCounts"]


@requires_db
def test_an_outcome_outside_the_window_is_not_this_metrics_conversion(db, tenant):
    """A ninety-day metric counting a conversion from month six is the arm
    that has been running longer, not the programme that worked."""
    from runtime.api import console

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        program_id = _program(cur, tid, "in-time", "positive_reply_14d")
        for _ in range(5):
            _enrol(cur, tid, program_id, "treatment", outcome="reply_positive", days_after=3)
        for _ in range(5):
            _enrol(cur, tid, program_id, "treatment", outcome="reply_positive", days_after=40)
        for _ in range(5):
            _enrol(cur, tid, program_id, "control")
        cur.execute("select * from tenant where id = %s", (tid,))
        view = console.build(cur, dict(cur.fetchone()))

    [program] = view["programs"]
    assert program["nTreat"] == 10
    # The arm's own rate, which is what `_rates` counts and what the verdict
    # rests on. Asserting the total alone let a mutation that dropped the
    # window clause from the arms survive: the unread-share query has its own
    # copy of it, and one of the two passing is not the guard.
    assert program["treat"] == 50.0, "a reply on day 40 is outside a fourteen-day metric"
    assert program["conversions"] == 5


@requires_db
def test_the_measurement_endpoint_says_what_it_measured(db, tenant):
    from fastapi.testclient import TestClient

    from runtime.api.app import create_app
    from runtime.provision import issue_api_key

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        program_id = _program(cur, tid, "endpoint", "opportunity_created_90d")
        _enrol(cur, tid, program_id, "treatment", outcome="opp_created")
        _enrol(cur, tid, program_id, "control")
    key = issue_api_key(db, tid, "test", []).token

    client = TestClient(create_app(db), raise_server_exceptions=False)
    body = client.get(f"/v1/programs/{program_id}/measurement",
                      headers={"x-api-key": key}).json()
    assert body["primary_metric"] == "opportunity_created_90d"
    assert body["metric_window_days"] == 90
    assert "opportunity" in body["metric_counts"]
    assert body["treatment"] == 1 and body["control"] == 1


@requires_db
def test_a_program_naming_a_metric_nothing_counts_is_never_stored(db, tenant):
    """Refused where a programme is stored, not where it is rendered
    (ADR-037), so signup and the command line get the same answer."""
    from runtime.engine.admission import NotAdmissible
    from runtime.repo import programs

    tid = str(tenant["id"])
    spec = {
        "experiment": {"holdout_pct": 10, "primary_metric": "vibes_7d"},
        "audience": {"sql": "select id as account_id from account"},
    }
    with db.tenant_tx(tid) as cur:
        with pytest.raises(NotAdmissible, match="vibes_7d"):
            programs.publish(cur, tid, key="bad-metric", version="1.0.0", spec=spec,
                             spec_hash="h")


@requires_db
def test_the_frozen_report_records_which_metric_it_was_made_of(db, tenant):
    """Two reports with the same arms and different metrics are two different
    claims, so the metric is part of the digest."""
    from runtime import metering, reporting

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        program_id = _program(cur, tid, "frozen", "signed_contract_60d")
        _enrol(cur, tid, program_id, "treatment", outcome="won")
        _enrol(cur, tid, program_id, "control")
        cur.execute("select * from tenant where id = %s", (tid,))
        row = dict(cur.fetchone())
        period = metering.open_period(cur, row)
        closed = metering.close_period(cur, row, str(period["id"]))
        [frozen] = reporting.freeze_all(cur, row, closed, frozen_by="test")

    assert frozen["body"]["primary_metric"] == "signed_contract_60d"
    assert frozen["body"]["metric_window_days"] == 60
    assert "signed_contract_60d" in frozen["rendered"]
