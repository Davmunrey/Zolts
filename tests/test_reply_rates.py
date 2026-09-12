"""Which copy works: reply and positive-reply rates per step, with the sample
beside the rate and no rate under the floor the experiment accepts.

Every sequencing tool shows an open rate. None shows a rate with the sample
size that makes it believable, and none withholds the rate when the sample
cannot carry one (`docs/28`, OX-4). The exit criterion is two halves of one
line: a step with fewer positive replies than `MIN_CONVERSIONS_PER_ARM`
shows the count and no rate, and a step with exactly that many shows the
rate. Both are tested at the rule and again against Postgres.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from runtime import replyrates as reader
from tests.conftest import requires_db
from zolts.experiment import MIN_CONVERSIONS_PER_ARM
from zolts.replyrates import FLOOR, StepCopy, copy_steps, rate, rows

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)

PLAY = {"plays": {
    "t1": {"steps": [{"step": "research_brief", "agent": "researcher"},
                     {"step": "email_1", "channel": "email", "wait": "0d"},
                     {"step": "call_revops", "channel": "task", "wait": "1d"},
                     {"step": "email_2", "channel": "email", "wait": "3d"}]},
    "t2": {"steps": [{"step": "email_1", "channel": "email"},
                     {"step": "linkedin_1", "channel": "linkedin"}]},
}}


# -- the rule ------------------------------------------------------------


def test_the_floor_is_the_experiments_floor_and_not_a_second_number():
    assert FLOOR == MIN_CONVERSIONS_PER_ARM


def test_below_the_floor_the_count_is_shown_and_no_rate():
    below = rate(FLOOR - 1, 100)
    assert below.value is None
    assert below.count == FLOOR - 1 and below.of == 100
    assert below.withheld_because == f"{FLOOR - 1} of {FLOOR} needed"


def test_at_the_floor_the_rate_appears():
    """The other half of the exit criterion: exactly the floor is enough."""
    at = rate(FLOOR, 100)
    assert at.value == FLOOR / 100 and at.withheld_because is None


def test_nothing_sent_is_said_rather_than_divided():
    none = rate(0, 0)
    assert none.value is None and none.withheld_because == "nothing sent"
    with pytest.raises(ValueError):
        rate(3, 2)


def test_copy_steps_follow_play_order_once_each_and_skip_human_tasks():
    assert copy_steps(PLAY) == ["email_1", "email_2", "linkedin_1"], (
        "a research step has no channel, a task step is not copy, and email_1 "
        "appears once although two plays name it")


def test_every_copy_step_has_a_row_even_when_nothing_was_sent():
    built = rows(PLAY, {("email_1", "treatment"): {"sent": 40, "replied": 6, "positive": 5}})
    assert [(r.step, r.variant) for r in built] == [
        ("email_1", "treatment"), ("email_2", "treatment"), ("linkedin_1", "treatment")]
    assert built[0].positive_rate.value == 0.125
    assert built[1].sent == 0 and built[1].reply_rate.withheld_because == "nothing sent"


def test_a_step_an_old_version_sent_still_has_its_row_after_the_plays_own():
    built = rows(PLAY, {("email_old", "treatment"): {"sent": 3, "replied": 1, "positive": 0}})
    assert [r.step for r in built] == ["email_1", "email_2", "linkedin_1", "email_old"]


def test_a_reply_the_step_did_not_send_is_refused():
    with pytest.raises(ValueError):
        StepCopy("email_1", "treatment", sent=2, replied=3, positive=0)


# -- against a real database ---------------------------------------------


def _program(cur, tenant_id, key="copy"):
    spec = {"plays": {"t1": {"steps": [{"step": "email_1", "channel": "email"},
                                       {"step": "email_2", "channel": "email", "wait": "3d"}]}},
            "experiment": {"holdout_pct": 10, "primary_metric": "positive_reply_14d"}}
    cur.execute("insert into program (tenant_id, key, version, spec, spec_hash, status)"
                " values (%s,%s,'1.0.0',%s,'h','live') returning id, spec",
                (tenant_id, key, json.dumps(spec)))
    row = cur.fetchone()
    return str(row["id"]), row["spec"]


def _enrol(cur, tenant_id, program, variant="treatment"):
    cur.execute("insert into account (tenant_id, name) values (%s,'A') returning id",
                (tenant_id,))
    account = cur.fetchone()["id"]
    cur.execute(
        "insert into enrollment (tenant_id, program_id, entity_type, entity_id,"
        " variant, tier, state, entered_at) values (%s,%s,'account',%s,%s,'t1',"
        " 'active',%s) returning id", (tenant_id, program, account, variant, NOW))
    return str(cur.fetchone()["id"])


def _touch(cur, tenant_id, enrol, step, at, status="sent"):
    cur.execute(
        "insert into touch (tenant_id, enrollment_id, channel, direction, step_key,"
        " idempotency_key, status, content, provider, sent_at)"
        " values (%s,%s,'email','out',%s,%s,%s,'{}','smartlead',%s)",
        (tenant_id, enrol, step, uuid.uuid4().hex, status, at))


def _positive(cur, tenant_id, enrol, at):
    cur.execute(
        "insert into outcome (tenant_id, enrollment_id, type, occurred_at, source)"
        " values (%s,%s,'reply_positive',%s,'smartlead')", (tenant_id, enrol, at))


def _row(built, step, variant="treatment"):
    return [r for r in built if r["step"] == step and r["variant"] == variant][0]


@requires_db
def test_a_step_under_the_floor_shows_the_count_and_a_step_at_it_shows_the_rate(db, tenant):
    """The exit criterion of OX-4, at the reader. email_1 collects exactly the
    floor's worth of positive replies out of ten sends; email_2 collects one
    fewer out of the same ten."""
    with db.tenant_tx(tenant["id"]) as cur:
        program, spec = _program(cur, tenant["id"])
        for i in range(10):
            enrol = _enrol(cur, tenant["id"], program)
            _touch(cur, tenant["id"], enrol, "email_1", NOW + timedelta(hours=1),
                   status="replied" if i < FLOOR else "sent")
            if i < FLOOR:
                _positive(cur, tenant["id"], enrol, NOW + timedelta(hours=2))
            _touch(cur, tenant["id"], enrol, "email_2", NOW + timedelta(days=3),
                   status="replied" if i < FLOOR - 1 else "sent")
            if i < FLOOR - 1:
                _positive(cur, tenant["id"], enrol, NOW + timedelta(days=3, hours=2))
        built = reader.for_program(cur, program, spec)

    first, second = _row(built, "email_1"), _row(built, "email_2")
    assert (first["sent"], first["replied"], first["positive"]) == (10, FLOOR, FLOOR)
    assert first["positiveRate"]["value"] == FLOOR / 10
    assert first["replyRate"]["value"] == FLOOR / 10
    assert (second["sent"], second["positive"]) == (10, FLOOR - 1)
    assert second["positiveRate"]["value"] is None
    assert second["positiveRate"]["withheldBecause"] == f"{FLOOR - 1} of {FLOOR} needed"


@requires_db
def test_a_positive_reply_is_credited_to_the_last_step_sent_before_it(db, tenant):
    """The runtime records the outcome on the enrolment, not on a step. A
    reply on day four, after email_1 on day zero and email_2 on day three,
    answered email_2."""
    with db.tenant_tx(tenant["id"]) as cur:
        program, spec = _program(cur, tenant["id"])
        enrol = _enrol(cur, tenant["id"], program)
        _touch(cur, tenant["id"], enrol, "email_1", NOW)
        _touch(cur, tenant["id"], enrol, "email_2", NOW + timedelta(days=3), status="replied")
        _positive(cur, tenant["id"], enrol, NOW + timedelta(days=4))
        built = reader.for_program(cur, program, spec)
    assert _row(built, "email_2")["positive"] == 1
    assert _row(built, "email_1")["positive"] == 0


@requires_db
def test_the_holdout_sends_nothing_and_says_so(db, tenant):
    with db.tenant_tx(tenant["id"]) as cur:
        program, spec = _program(cur, tenant["id"])
        _enrol(cur, tenant["id"], program, variant="control")
        treated = _enrol(cur, tenant["id"], program)
        _touch(cur, tenant["id"], treated, "email_1", NOW)
        built = reader.for_program(cur, program, spec)
    assert [(r["step"], r["variant"]) for r in built] == [
        ("email_1", "treatment"), ("email_2", "treatment")]
    assert _row(built, "email_1")["sent"] == 1
    assert _row(built, "email_2")["replyRate"]["withheldBecause"] == "nothing sent"


@requires_db
def test_a_touch_that_was_never_sent_is_not_a_sample(db, tenant):
    """A queued or failed touch is not copy anybody read."""
    with db.tenant_tx(tenant["id"]) as cur:
        program, spec = _program(cur, tenant["id"])
        enrol = _enrol(cur, tenant["id"], program)
        _touch(cur, tenant["id"], enrol, "email_1", None, status="queued")
        _touch(cur, tenant["id"], enrol, "email_1", NOW, status="failed")
        built = reader.for_program(cur, program, spec)
    assert _row(built, "email_1")["sent"] == 0


@requires_db
def test_the_programme_view_and_the_endpoint_carry_the_rows(db, tenant):
    from fastapi.testclient import TestClient

    from runtime.api import console as console_view
    from runtime.api.app import create_app
    from runtime.provision import issue_api_key

    with db.tenant_tx(tenant["id"]) as cur:
        program, spec = _program(cur, tenant["id"])
        enrol = _enrol(cur, tenant["id"], program)
        _touch(cur, tenant["id"], enrol, "email_1", NOW, status="replied")
        _positive(cur, tenant["id"], enrol, NOW + timedelta(hours=1))
        view = console_view.build(cur, tenant)
    mine = [p for p in view["programs"] if p["key"] == "copy"][0]
    assert _row(mine["copy"], "email_1")["positive"] == 1

    client = TestClient(create_app(db), raise_server_exceptions=False)
    token = issue_api_key(db, str(tenant["id"]), "test", []).token
    answer = client.get(f"/v1/programs/{program}/copy", headers={"x-api-key": token})
    assert answer.status_code == 200, answer.text
    assert answer.json()["rows"] == mine["copy"]
    missing = client.get(f"/v1/programs/{uuid.uuid4()}/copy", headers={"x-api-key": token})
    assert missing.status_code == 404


def test_the_surface_withholds_a_rate_the_rule_withheld():
    """The screen prints the count and the reason, never a rate of its own."""
    from pathlib import Path

    surface = (Path(__file__).resolve().parent.parent / "design" / "console.html"
               ).read_text(encoding="utf-8")
    body = surface[surface.index("function copyBlock("):]
    body = body[:body.index("\n}")]
    assert "withheldBecause" in body, "the block never says why a rate is missing"
    assert "positive / " not in body and "replied / " not in body, (
        "the surface divides for itself instead of rendering the rule's rate")
