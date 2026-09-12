"""Which signals earn their keep: one funnel per catalogue signal, descriptive.

`docs/06` prices signals by tier and gives each a half-life, a freshness SLA
and a legal basis. After a month the operator's first question is *which of
these is worth paying for*, and nothing answered it (`docs/28`, OX-3).

Two properties are the exit criterion, and both are tested at the rule and
again against Postgres. **Every catalogue signal has a row, the silent ones
included:** a signal that fired zero times is the row the operator is paying
for and getting nothing from. **A conversion counts inside the programme's
declared window and nowhere else:** the day of enrolment is inside, the
declared day is not, and the day before enrolment is not the signal's doing.

Descriptive, not attributed. The holdout converts too and is counted; whether
the programme caused it is the incrementality report's question, and `docs/06`
SIG-2 stays blocked on that instrument.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from runtime import signalfunnel as reader
from tests.conftest import requires_db
from zolts import signals
from zolts.signalfunnel import STAGES, FunnelError, Row, in_window, order, rows

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def _catalogue(*keys: str) -> dict:
    return {k: SimpleNamespace(name=k.replace(".", " "), tier="B") for k in keys}


# -- the rule ------------------------------------------------------------


def test_every_catalogue_signal_has_a_row_including_the_ones_that_never_fired():
    built = rows(_catalogue("a.one", "b.two", "c.three"),
                 {"a.one": {"fired": 4, "enrolled": 2}})
    assert [r.key for r in built] == ["a.one", "b.two", "c.three"]
    silent = [r for r in built if r.key != "a.one"]
    assert all(r.silent and r.catalogued for r in silent)
    assert all((r.fired, r.enrolled, r.reached, r.converted) == (0, 0, 0, 0)
               for r in silent)


def test_a_signal_that_fired_outside_the_catalogue_is_shown_and_marked():
    """Money spent on an undefined signal is still money."""
    built = rows(_catalogue("a.one"), {"custom.push": {"fired": 3}})
    custom = [r for r in built if r.key == "custom.push"][0]
    assert custom.catalogued is False
    assert custom.name == "custom.push" and custom.tier is None
    assert custom.fired == 3


def test_the_window_is_half_open_at_enrolment_and_closed_at_the_declared_day():
    entered = NOW
    assert in_window(entered, entered, 30), "the moment of enrolment is inside"
    assert in_window(entered, entered + timedelta(days=29, hours=23), 30)
    assert not in_window(entered, entered + timedelta(days=30), 30), (
        "day thirty of a thirty-day window is the next period's")
    assert not in_window(entered, entered - timedelta(seconds=1), 30), (
        "an outcome before enrolment is not the signal's doing")


def test_the_best_earning_signal_sorts_first_and_silence_sorts_last_by_key():
    built = order([
        Row("z.silent", "z", "B", True),
        Row("m.fired", "m", "B", True, fired=9),
        Row("a.silent", "a", "B", True),
        Row("k.converted", "k", "A", True, fired=1, enrolled=1, converted=1),
        Row("r.reached", "r", "A", True, fired=5, enrolled=3, reached=2),
    ])
    assert [r.key for r in built] == ["k.converted", "r.reached", "m.fired",
                                      "a.silent", "z.silent"]


def test_columns_that_disagree_are_refused_not_rendered():
    with pytest.raises(FunnelError):
        Row("x", "x", None, True, enrolled=2, held_out=1, reached=2)
    with pytest.raises(FunnelError):
        Row("x", "x", None, True, enrolled=1, converted=2)
    with pytest.raises(FunnelError):
        Row("x", "x", None, True, enrolled=1, held_out=2)


def test_every_stage_says_what_it_counts():
    """A number without a definition is a number two people read differently
    in the same meeting. The sentence ships with the number."""
    for stage in STAGES:
        assert stage.label and len(stage.counts.split()) >= 5, stage.key
    assert [s.key for s in STAGES] == ["fired", "listened", "enrolled", "heldOut",
                                       "reached", "converted"]


def test_the_shipped_catalogue_is_what_the_funnel_rows_are_built_from():
    """The premise of the exit criterion: the catalogue this repository ships
    has signals in it, so *every catalogue signal has a row* is a claim about
    something."""
    shipped = signals.catalogue()
    assert len(shipped) >= 5
    built = rows(shipped, {})
    assert {r.key for r in built} == set(shipped)
    assert all(r.silent for r in built)


# -- against a real database ---------------------------------------------


def _program(cur, tenant_id, key, *, metric="meeting_booked_30d", status="live",
             signal="hiring.role_opened"):
    spec = {"trigger": {"events": [{"signal": signal}], "window": "30d"},
            "experiment": {"holdout_pct": 10, "primary_metric": metric}}
    cur.execute("insert into program (tenant_id, key, version, spec, spec_hash,"
                " status) values (%s,%s,'1.0.0',%s,'h',%s) returning id",
                (tenant_id, key, json.dumps(spec), status))
    return str(cur.fetchone()["id"])


def _account(cur, tenant_id, name):
    cur.execute("insert into account (tenant_id, name) values (%s,%s) returning id",
                (tenant_id, name))
    return str(cur.fetchone()["id"])


def _signal(cur, tenant_id, account, type="hiring.role_opened", at=NOW):
    cur.execute(
        "insert into signal (tenant_id, entity_type, entity_id, type, strength,"
        " half_life_h, source, legal_basis, payload, observed_at)"
        " values (%s,'account',%s,%s,0.6,720,'test','legitimate_interest','{}',%s)"
        " returning id", (tenant_id, account, type, at))
    return str(cur.fetchone()["id"])


def _enrol(cur, tenant_id, program, account, signal, *, variant="treatment", at=NOW):
    cur.execute(
        "insert into enrollment (tenant_id, program_id, entity_type, entity_id,"
        " variant, tier, state, context, entered_at)"
        " values (%s,%s,'account',%s,%s,'A',%s,%s,%s) returning id",
        (tenant_id, program, account, variant,
         "active" if variant == "treatment" else "control",
         json.dumps({"signal_id": signal}), at))
    return str(cur.fetchone()["id"])


def _touch(cur, tenant_id, enrol, at=NOW + timedelta(hours=2), status="sent"):
    cur.execute(
        "insert into touch (tenant_id, enrollment_id, channel, direction, step_key,"
        " idempotency_key, status, content, provider, sent_at)"
        " values (%s,%s,'email','out','email_1',%s,%s,'{}','smartlead',%s)",
        (tenant_id, enrol, uuid.uuid4().hex, status, at))


def _outcome(cur, tenant_id, enrol, type, at):
    cur.execute(
        "insert into outcome (tenant_id, enrollment_id, type, occurred_at, source)"
        " values (%s,%s,%s,%s,'test')", (tenant_id, enrol, type, at))


def _row(funnel, key):
    return [r for r in funnel["rows"] if r["key"] == key][0]


@requires_db
def test_a_conversion_is_counted_inside_the_declared_window_and_not_outside(db, tenant):
    """The exit criterion, at the reader: three enrolments from one signal
    type under a thirty-day metric. One converts on day twenty-nine, one on
    day thirty exactly, one with the wrong kind of outcome on day one, and the
    first also carries an outcome from before it was enrolled."""
    with db.tenant_tx(tenant["id"]) as cur:
        program = _program(cur, tenant["id"], "hiring-30", metric="meeting_booked_30d")
        a, b, c = (_account(cur, tenant["id"], n) for n in ("A", "B", "C"))
        sa, sb, sc = (_signal(cur, tenant["id"], x) for x in (a, b, c))
        ea = _enrol(cur, tenant["id"], program, a, sa)
        eb = _enrol(cur, tenant["id"], program, b, sb)
        ec = _enrol(cur, tenant["id"], program, c, sc)
        _touch(cur, tenant["id"], ea)
        _touch(cur, tenant["id"], eb)
        _outcome(cur, tenant["id"], ea, "meeting", NOW + timedelta(days=29))
        _outcome(cur, tenant["id"], ea, "meeting", NOW - timedelta(hours=1))
        _outcome(cur, tenant["id"], eb, "meeting", NOW + timedelta(days=30))
        _outcome(cur, tenant["id"], ec, "opp_created", NOW + timedelta(days=1))
        funnel = reader.for_tenant(cur, now=NOW)

    row = _row(funnel, "hiring.role_opened")
    assert row["fired"] == 3 and row["listened"] == 1
    assert row["enrolled"] == 3 and row["heldOut"] == 0
    assert row["reached"] == 2, "two of the three had a touch sent"
    assert row["converted"] == 1, (
        "day twenty-nine counts once; day thirty, the day before enrolment and "
        "an opportunity under a meetings metric do not")
    assert row["windows"] == [30]


@requires_db
def test_each_programme_is_judged_by_its_own_window_not_a_shared_one(db, tenant):
    """Two programmes listen to the same signal, one at thirty days and one
    at ninety. The same day-forty-five conversion is outside one and inside
    the other, and the row says both windows are in play."""
    with db.tenant_tx(tenant["id"]) as cur:
        thirty = _program(cur, tenant["id"], "p30", metric="meeting_booked_30d")
        ninety = _program(cur, tenant["id"], "p90", metric="qualified_meeting_30d")
        cur.execute("update program set spec = jsonb_set(spec, '{experiment,primary_metric}',"
                    " '\"opportunity_created_90d\"') where id = %s", (ninety,))
        a, b = _account(cur, tenant["id"], "A"), _account(cur, tenant["id"], "B")
        sa, sb = _signal(cur, tenant["id"], a), _signal(cur, tenant["id"], b)
        ea = _enrol(cur, tenant["id"], thirty, a, sa)
        eb = _enrol(cur, tenant["id"], ninety, b, sb)
        _outcome(cur, tenant["id"], ea, "meeting", NOW + timedelta(days=45))
        _outcome(cur, tenant["id"], eb, "opp_created", NOW + timedelta(days=45))
        funnel = reader.for_tenant(cur, now=NOW)

    row = _row(funnel, "hiring.role_opened")
    assert row["enrolled"] == 2 and row["listened"] == 2
    assert row["converted"] == 1, "day forty-five is inside ninety and outside thirty"
    assert row["windows"] == [30, 90]


@requires_db
def test_the_holdout_is_counted_as_enrolled_and_held_out_and_never_reached(db, tenant):
    with db.tenant_tx(tenant["id"]) as cur:
        program = _program(cur, tenant["id"], "held")
        a, b = _account(cur, tenant["id"], "A"), _account(cur, tenant["id"], "B")
        sa, sb = _signal(cur, tenant["id"], a), _signal(cur, tenant["id"], b)
        _enrol(cur, tenant["id"], program, a, sa, variant="treatment")
        eb = _enrol(cur, tenant["id"], program, b, sb, variant="control")
        # The control arm converts on its own, and that is counted: this is a
        # funnel, not an attribution.
        _outcome(cur, tenant["id"], eb, "meeting", NOW + timedelta(days=3))
        funnel = reader.for_tenant(cur, now=NOW)

    row = _row(funnel, "hiring.role_opened")
    assert (row["enrolled"], row["heldOut"], row["reached"], row["converted"]) == (2, 1, 0, 1)


@requires_db
def test_a_signal_that_never_fired_has_a_row_of_zeros_beside_the_ones_that_did(db, tenant):
    with db.tenant_tx(tenant["id"]) as cur:
        a = _account(cur, tenant["id"], "A")
        _signal(cur, tenant["id"], a, type="funding.round")
        funnel = reader.for_tenant(cur, now=NOW)

    keys = [r["key"] for r in funnel["rows"]]
    assert set(signals.catalogue()) <= set(keys), "every catalogue signal has a row"
    assert keys[0] == "funding.round", "the only one that fired sorts first"
    silent = _row(funnel, "hiring.role_opened")
    assert silent["fired"] == 0 and silent["enrolled"] == 0 and silent["converted"] == 0
    assert silent["catalogued"] is True and silent["tier"] == "B"


@requires_db
def test_only_a_live_programme_listens(db, tenant):
    with db.tenant_tx(tenant["id"]) as cur:
        _program(cur, tenant["id"], "live-one", status="live")
        _program(cur, tenant["id"], "paused-one", status="paused")
        _program(cur, tenant["id"], "draft-one", status="draft")
        _program(cur, tenant["id"], "other", status="live", signal="funding.round")
        funnel = reader.for_tenant(cur, now=NOW)
    assert _row(funnel, "hiring.role_opened")["listened"] == 1
    assert _row(funnel, "funding.round")["listened"] == 1
    assert _row(funnel, "product.limit_hit")["listened"] == 0


@requires_db
def test_the_funnel_reads_what_ingest_writes(db, tenant):
    """Through the real trigger rather than a hand-written context, so the
    key the reader joins on is the key the runtime writes."""
    from runtime.engine import enroll
    from runtime.repo import programs

    spec = {
        "trigger": {"events": [{"signal": "funding.round"}], "window": "30d"},
        "audience": {"sql": "select id as account_id from account"},
        "score": {"floor": 0},
        "route": {"tiers": [{"key": "t1", "capacity_per_week": 100}]},
        "plays": {"t1": {"steps": [{"step": "email_1", "channel": "email", "wait": "1d"}]}},
        "experiment": {"holdout_pct": 10, "unit": "account",
                       "primary_metric": "signed_contract_60d"},
    }
    with db.tenant_tx(tenant["id"]) as cur:
        published = programs.publish(cur, tenant["id"], key="ingested", version="1.0.0",
                                     spec=spec, spec_hash="ingested-hash",
                                     metadata={"name": "ingested"})
        programs.activate(cur, str(published["id"]))
        made = []
        for i in range(3):
            account = _account(cur, tenant["id"], f"Acme {i}")
            result = enroll.ingest(
                cur, tenant["id"], entity_type="account", entity_id=account,
                type="funding.round", strength=0.9, half_life_h=72, source="test",
                legal_basis="legitimate_interest", payload={}, observed_at=NOW, now=NOW)
            made.extend(e for e in result.enrollments if e.program_key == "ingested")
        funnel = reader.for_tenant(cur, now=NOW)

    assert len(made) == 3, "the premise: every account enrolled"
    row = _row(funnel, "funding.round")
    assert row["fired"] == 3 and row["enrolled"] == 3
    assert row["heldOut"] == sum(1 for e in made if e.variant == "control")
    assert row["windows"] == [60]


@requires_db
def test_another_tenants_signals_are_not_this_tenants_funnel(db, tenant, other_tenant):
    with db.tenant_tx(other_tenant["id"]) as cur:
        a = _account(cur, other_tenant["id"], "Theirs")
        _signal(cur, other_tenant["id"], a)
    with db.tenant_tx(tenant["id"]) as cur:
        funnel = reader.for_tenant(cur, now=NOW)
    assert _row(funnel, "hiring.role_opened")["fired"] == 0


@requires_db
def test_the_endpoint_and_the_console_carry_the_funnel(db, tenant):
    from fastapi.testclient import TestClient

    from runtime.api import console as console_view
    from runtime.api.app import create_app
    from runtime.provision import issue_api_key

    with db.tenant_tx(tenant["id"]) as cur:
        a = _account(cur, tenant["id"], "A")
        _signal(cur, tenant["id"], a)
        view = console_view.signals_view(cur)
    client = TestClient(create_app(db), raise_server_exceptions=False)
    token = issue_api_key(db, str(tenant["id"]), "test", []).token
    answer = client.get("/v1/signals/funnel", headers={"x-api-key": token})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["kind"] == "signal_funnel"
    assert [s["key"] for s in body["stages"]] == [s.key for s in STAGES]
    assert _row(body, "hiring.role_opened")["fired"] == 1
    assert [r["key"] for r in view["funnel"]["rows"]] == [r["key"] for r in body["rows"]]


def test_the_surface_renders_the_funnel_from_the_stages_it_is_sent():
    """No second list of stage names in the markup: the screen renders the
    stages the rule ships, so a stage added here is a stage rendered there."""
    from pathlib import Path

    surface = (Path(__file__).resolve().parent.parent / "design" / "console.html"
               ).read_text(encoding="utf-8")
    assert "function funnelBlock(" in surface
    assert 'sect("Earning their keep"' in surface
    assert ".row[data-f]" in surface, "a funnel row is not selectable"
    body = surface[surface.index("function funnelBlock("):]
    body = body[:body.index("\n}")]
    for stage in STAGES:
        assert f'"{stage.label}"' not in body, (
            f"the funnel block restates the label {stage.label!r} instead of "
            f"rendering the one the rule ships")
