"""Going and looking, and what it costs to keep looking.

`docs/06` opens by calling signal-to-action latency the highest-leverage
variable in the whole GTM system and a product SLA rather than an
implementation detail. Nothing watched anything: a signal reached this runtime
only when the customer pushed one, so the product learned about a funding round
when its customer already knew, and the SLA was a claim about somebody else's
work.

These tests are about the two decisions that make watching affordable and
honest — a check billed per account-day rather than per check, and a detection
past its freshness SLA refused rather than acted on.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from runtime import watch
from runtime.connectors import signalsource
from runtime.connectors.fake import FakeSignalSource
from tests.conftest import requires_db
from tests.test_runtime_engine import DISPATCH_SPEC, _account_with_contact, _publish
from zolts.signals import (SignalDefinition, SignalDefinitionError, catalogue,
                           duration, parse)

NOW = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(signalsource._SOURCES)
    signalsource._SOURCES.clear()
    yield
    signalsource._SOURCES.clear()
    signalsource._SOURCES.update(saved)


def _spec(signal="funding.round"):
    """A live program waiting on one signal."""
    return {**DISPATCH_SPEC,
            "trigger": {**DISPATCH_SPEC["trigger"],
                        "events": [{"signal": signal}]}}


def _source(connector="press_feed", **kwargs):
    source = FakeSignalSource(connector=connector, **kwargs)
    signalsource.register_source(source)
    return source


def _tenant_row(cur, tid):
    cur.execute("select * from tenant where id = %s", (tid,))
    return cur.fetchone()


# -- the catalogue -------------------------------------------------------

def test_every_signal_a_shipped_program_triggers_on_is_defined():
    """A program triggering on a signal nobody defined is a program that will
    never fire, and the operator who published it believes it will."""
    import glob

    import yaml

    defined = set(catalogue())
    triggered = set()
    for path in glob.glob("examples/programs/*.yaml"):
        spec = (yaml.safe_load(open(path)) or {}).get("spec", {})
        for event in (spec.get("trigger") or {}).get("events") or []:
            if event.get("signal"):
                triggered.add(event["signal"])

    assert triggered, "no shipped program triggers on anything"
    assert triggered <= defined, (
        f"programs trigger on signals with no definition: "
        f"{sorted(triggered - defined)}")


def test_a_duration_is_hours_or_days_and_nothing_else():
    """Minutes invite a refresh that costs more in checks than the signal is
    worth; weeks are ambiguous enough that somebody has to look them up."""
    assert duration("6h") == timedelta(hours=6)
    assert duration("30d") == timedelta(days=30)
    for bad in ("6m", "0h", "-1d", "", "soon", "1w"):
        with pytest.raises(SignalDefinitionError):
            duration(bad)


def test_a_definition_without_cost_decay_or_basis_is_refused():
    """docs/06: a signal missing any of the three can be neither budgeted nor
    audited."""
    base = {"apiVersion": "zolts/v1", "kind": "SignalDefinition",
            "metadata": {"key": "test.thing"},
            "spec": {"entity": "account", "source": {"connector": "f"},
                     "freshness_sla": "12h", "half_life_h": 24,
                     "base_strength": 0.5, "legal_basis": "legitimate_interest"}}
    assert parse(base).key == "test.thing"
    for missing in ("half_life_h", "base_strength", "legal_basis", "freshness_sla"):
        broken = {**base, "spec": {k: v for k, v in base["spec"].items() if k != missing}}
        with pytest.raises(SignalDefinitionError, match=missing):
            parse(broken)


def test_a_missing_catalogue_raises_rather_than_watching_nothing():
    """An empty catalogue and an absent one look identical to a caller and
    mean opposite things."""
    with pytest.raises(SignalDefinitionError, match="detects nothing"):
        catalogue("/nonexistent/catalogue")


def test_freshness_is_measured_from_when_it_happened():
    definition = SignalDefinition(
        key="k", entity="account", connector="c", refresh=timedelta(hours=6),
        freshness_sla=timedelta(hours=4), half_life_h=48, base_strength=0.6,
        legal_basis="legitimate_interest", dedupe_window=timedelta(days=7))
    assert definition.is_fresh(NOW - timedelta(hours=3), NOW)
    assert not definition.is_fresh(NOW - timedelta(hours=5), NOW)
    assert definition.due(None, NOW), "an account never checked is due"
    assert not definition.due(NOW - timedelta(hours=1), NOW)
    assert definition.due(NOW - timedelta(hours=7), NOW)


# -- only what a program is waiting on -----------------------------------

@requires_db
def test_nothing_is_watched_that_no_live_program_wants(db, tenant):
    """Watching a signal no program consumes is spending a data budget to fill
    a table."""
    tid = str(tenant["id"])
    source = _source()
    with db.tenant_tx(tid) as cur:
        _account_with_contact(cur, tid)
        empty = watch.once(cur, _tenant_row(cur, tid), now=NOW)

        _publish(cur, tid, spec=_spec(), key="funded")
        after = watch.once(cur, _tenant_row(cur, tid), now=NOW)

    assert empty.signals == []
    assert source.asked == [("funding.round", 1)]
    assert [w.signal_key for w in after.signals] == ["funding.round"]


@requires_db
def test_a_program_waiting_on_an_undefined_signal_is_reported(db, tenant):
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _account_with_contact(cur, tid)
        _publish(cur, tid, spec=_spec("invented.signal"), key="invented")
        result = watch.once(cur, _tenant_row(cur, tid), now=NOW)

    assert result.signals[0].error is not None
    assert "no definition" in result.signals[0].error
    assert result.credits == 0.0


# -- what it costs -------------------------------------------------------

@requires_db
def test_a_check_is_billed_once_per_account_per_day(db, tenant):
    """docs/12 prices it per account per day, and the consequence is the point:
    a definition refreshing every six hours costs the same as one refreshing
    daily, so the fast refresh a Tier A signal needs is affordable."""
    tid = str(tenant["id"])
    # hiring.role_opened refreshes every 6h, so a second check can fall on the
    # same UTC day. A signal refreshing daily could not show this at all, which
    # is what the first version of this test got wrong.
    _source(connector="jobs_feed", detects=[])
    with db.tenant_tx(tid) as cur:
        _account_with_contact(cur, tid)
        _publish(cur, tid, spec=_spec("hiring.role_opened"), key="hiring")

        first = watch.once(cur, _tenant_row(cur, tid), now=NOW)
        # Past the six-hour refresh, still 4 May.
        later = watch.once(cur, _tenant_row(cur, tid), now=NOW + timedelta(hours=7))
        # 5 May.
        tomorrow = watch.once(cur, _tenant_row(cur, tid), now=NOW + timedelta(days=1))

        cur.execute("select sum(billed_credits) as c from cost_event"
                    " where kind = 'signal.check'")
        billed = float(cur.fetchone()["c"] or 0)

    assert first.credits == 0.5
    assert later.credits == 0.0, "a second check on the same day was billed"
    assert tomorrow.credits == 0.5
    assert billed == 1.0


@requires_db
def test_an_account_checked_for_two_signals_pays_once(db, tenant):
    """The unit is the account-day, not the signal."""
    tid = str(tenant["id"])
    _source(connector="press_feed", detects=[])
    _source(connector="jobs_feed", detects=[])
    with db.tenant_tx(tid) as cur:
        _account_with_contact(cur, tid)
        _publish(cur, tid, spec=_spec("funding.round"), key="a")
        _publish(cur, tid, spec=_spec("hiring.role_opened"), key="b")
        result = watch.once(cur, _tenant_row(cur, tid), now=NOW)

    assert {w.signal_key for w in result.signals} == {"funding.round",
                                                     "hiring.role_opened"}
    assert sum(w.checked for w in result.signals) == 2
    assert result.credits == 0.5, "two signals on one account cost two checks"


@requires_db
def test_an_account_is_not_re_examined_before_its_refresh(db, tenant):
    tid = str(tenant["id"])
    source = _source(detects=[])
    with db.tenant_tx(tid) as cur:
        _account_with_contact(cur, tid)
        _publish(cur, tid, spec=_spec(), key="funded")
        watch.once(cur, _tenant_row(cur, tid), now=NOW)
        again = watch.once(cur, _tenant_row(cur, tid), now=NOW + timedelta(hours=1))

    assert len(source.asked) == 1, "the source was asked inside the refresh window"
    assert again.signals[0].checked == 0


# -- freshness -----------------------------------------------------------

@requires_db
def test_a_detection_past_its_sla_is_refused_and_counted(db, tenant):
    """Acting on a three-day-old event with a 48-hour half-life spends a touch
    on somebody whose moment has gone. Counted rather than dropped, because a
    source that keeps finding things too late is one to replace."""
    tid = str(tenant["id"])
    # hiring.role_opened: 12h freshness SLA.
    _source(connector="jobs_feed", observed_at=NOW - timedelta(hours=20))
    with db.tenant_tx(tid) as cur:
        _account_with_contact(cur, tid)
        _publish(cur, tid, spec=_spec("hiring.role_opened"), key="hiring")
        result = watch.once(cur, _tenant_row(cur, tid), now=NOW)

        cur.execute("select count(*) as n from signal")
        signals_recorded = cur.fetchone()["n"]
        cur.execute("select stale, detected from signal_check")
        recorded = cur.fetchone()

    watched = result.signals[0]
    assert watched.stale == 1 and watched.detected == 0 and watched.enrolled == 0
    assert signals_recorded == 0, "a stale detection was ingested anyway"
    assert recorded["detected"] is True and recorded["stale"] is True


@requires_db
def test_a_fresh_detection_enrolls_with_the_definitions_own_terms(db, tenant):
    """The strength, the decay and the legal basis come from the definition.
    A signal ingested with somebody's guess at its half-life decays wrongly for
    as long as it lives."""
    tid = str(tenant["id"])
    _source(observed_at=NOW - timedelta(hours=2), confidence=0.5)
    with db.tenant_tx(tid) as cur:
        _account_with_contact(cur, tid)
        _publish(cur, tid, spec=_spec(), key="funded")
        result = watch.once(cur, _tenant_row(cur, tid), now=NOW)

        cur.execute("select type, strength, half_life_h, legal_basis, source,"
                    " observed_at from signal")
        recorded = cur.fetchone()

    assert result.signals[0].detected == 1
    assert recorded["type"] == "funding.round"
    assert recorded["half_life_h"] == 720, "the catalogue's half-life was not used"
    assert recorded["legal_basis"] == "legitimate_interest"
    assert recorded["source"] == "press_feed"
    # base_strength 0.7 scaled by the source's own 0.5 confidence.
    assert abs(float(recorded["strength"]) - 0.35) < 1e-9
    assert recorded["observed_at"] == NOW - timedelta(hours=2), (
        "the time we noticed was recorded instead of the time it happened")


# -- a source that is down is not a quiet week ---------------------------

@requires_db
def test_a_source_that_errors_records_no_checks_and_bills_nothing(db, tenant):
    """A trail of checks that found nothing is what the refresh clock reads,
    so an outage would silence the signal until the window passed again."""
    tid = str(tenant["id"])
    _source(error_times=5)
    with db.tenant_tx(tid) as cur:
        _account_with_contact(cur, tid)
        _publish(cur, tid, spec=_spec(), key="funded")
        result = watch.once(cur, _tenant_row(cur, tid), now=NOW)

        cur.execute("select count(*) as n from signal_check")
        checks = cur.fetchone()["n"]

    assert result.signals[0].error is not None
    assert result.credits == 0.0
    assert checks == 0, "an outage left a trail that looks like a quiet week"


@requires_db
def test_an_unregistered_connector_is_reported_rather_than_raised(db, tenant):
    """One broken source must not stop the pass over the others."""
    tid = str(tenant["id"])
    _source(connector="jobs_feed", detects=[])
    with db.tenant_tx(tid) as cur:
        _account_with_contact(cur, tid)
        _publish(cur, tid, spec=_spec("funding.round"), key="a")   # press_feed absent
        _publish(cur, tid, spec=_spec("hiring.role_opened"), key="b")
        result = watch.once(cur, _tenant_row(cur, tid), now=NOW)

    by_key = {w.signal_key: w for w in result.signals}
    assert "no signal source" in by_key["funding.round"].error
    assert by_key["hiring.role_opened"].error is None
    assert by_key["hiring.role_opened"].checked == 1


# -- the SLA the document says matters most ------------------------------

@requires_db
def test_time_to_touch_is_measured_from_the_signal_not_the_enrollment(db, tenant, fake):
    """docs/06 defines it as signal to action; the console measured it from the
    enrollment, which for a detected signal is however long the source took to
    notice. Splitting it is more useful than either half: a bad p95 is either a
    source to change or workers to add."""
    from runtime.engine.worker import Worker
    from runtime.provision import store_connection
    from tests.conftest import SECRET

    tid = str(tenant["id"])
    _source(observed_at=NOW - timedelta(hours=3))
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _account_with_contact(cur, tid)
        _publish(cur, tid, spec=_spec(), key="funded")
        watch.once(cur, _tenant_row(cur, tid), now=NOW)

    assert Worker(db, secret_key=SECRET).tick([tid]).succeeded == 1

    with db.tenant_tx(tid) as cur:
        measured = watch.latency(cur)

    assert measured["touches"] == 1
    # The source took three hours to notice; the runtime acted at once.
    assert measured["detectionP95Minutes"] >= 179
    assert measured["executionP95Minutes"] < 60
    assert measured["totalP95Minutes"] >= measured["detectionP95Minutes"]


@requires_db
def test_latency_reports_nothing_rather_than_zero_when_nothing_was_sent(db, tenant):
    """Zero minutes and no data look identical on a dashboard and are opposite
    in meaning."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        assert watch.latency(cur) == {"touches": 0, "detectionP95Minutes": None,
                                      "executionP95Minutes": None,
                                      "totalP95Minutes": None}
