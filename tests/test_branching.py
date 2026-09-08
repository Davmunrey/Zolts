"""A play that reads what the contact did.

Every enrollment walked every step regardless of what happened. A breakup email
to somebody mid-conversation is the shape of automation a buyer points at when
they say these tools embarrass them.

A step's `when` uses the same expression language as triggers and exit rules. A
second condition language is a second set of rules to get wrong.
"""

from __future__ import annotations

import uuid

from runtime.engine import planner
from tests.conftest import requires_db

PROGRAM = {
    "trigger": {"events": [{"signal": "s"}], "combine": "any_within", "window": "30d",
                "dedupe": {"key": "account_id", "cooldown": "1d"}},
    "audience": {"sql": "select id as account_id from account"},
    "score": {"model": "m", "weights": {"fit": 1.0}, "floor": 0},
    "route": {"tiers": [{"key": "t2", "when": "score >= 0", "capacity_per_week": 100}]},
    "experiment": {"holdout_pct": 0, "unit": "account", "salt": "branch",
                   "primary_metric": "m"},
    "budget": {"monthly_credits": 1000, "on_exceed": "pause_and_alert"},
    "policy": {"inherit": "tenant_default"},
    "plays": {"t2": {"template": "t", "auto_send": True, "steps": [
        {"step": "email_1", "channel": "email", "wait": "0d"},
        {"step": "email_2", "channel": "email", "wait": "0d",
         "when": "not engagement.has_replied"},
        {"step": "breakup", "channel": "email", "wait": "0d",
         "when": "engagement.no_response"},
    ]}},
}


def _seed(db, tenant, *, replied=False, opened=False):
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        cur.execute(
            "insert into program (tenant_id, key, version, spec, spec_hash, status)"
            " values (%s,'branching',1,%s,'h','live') returning *",
            (tid, __import__("json").dumps(PROGRAM)))
        program = dict(cur.fetchone())
        cur.execute(
            "insert into enrollment (tenant_id, program_id, entity_type, entity_id,"
            " variant, tier, state, step_index) values (%s,%s,'account',"
            " gen_random_uuid(),'treatment','t2','running',1) returning *",
            (tid, program["id"]))
        enrollment = dict(cur.fetchone())
        if replied or opened:
            cur.execute(
                "insert into touch (tenant_id, enrollment_id, channel, step_key,"
                " idempotency_key, status) values (%s,%s,'email','email_1',%s,%s)",
                (tid, enrollment["id"], f"t-{uuid.uuid4().hex}",
                 "replied" if replied else "opened"))
    return tid, program, enrollment


@requires_db
def test_a_step_with_no_condition_always_runs(db, tenant):
    tid, program, enrollment = _seed(db, tenant)
    with db.tenant_tx(tid) as cur:
        planned = planner.plan_next(cur, tid, enrollment, program)
    assert planned is not None
    assert planned.step_key == "email_2"


@requires_db
def test_a_contact_who_replied_skips_the_chase_and_the_breakup(db, tenant):
    """Both remaining steps exclude a replier, so the sequence is finished
    rather than walked one tick at a time."""
    tid, program, enrollment = _seed(db, tenant, replied=True)
    with db.tenant_tx(tid) as cur:
        planned = planner.plan_next(cur, tid, enrollment, program)
        cur.execute("select state, exit_reason from enrollment where id = %s",
                    (enrollment["id"],))
        row = cur.fetchone()

    assert planned is None
    assert row["exit_reason"] == "sequence_complete"


@requires_db
def test_a_contact_who_opened_gets_the_chase_but_not_the_breakup(db, tenant):
    """`no_response` is not the same as `has not replied`. Flattening the two
    would send a breakup to somebody who is reading."""
    tid, program, enrollment = _seed(db, tenant, opened=True)
    with db.tenant_tx(tid) as cur:
        planned = planner.plan_next(cur, tid, enrollment, program)
    assert planned is not None
    assert planned.step_key == "email_2"


@requires_db
def test_engagement_is_read_from_the_record_rather_than_a_counter(db, tenant):
    """A counter is a second source of truth that drifts. These two tables are
    already what an audit reads."""
    tid, _, enrollment = _seed(db, tenant, replied=True)
    with db.tenant_tx(tid) as cur:
        seen = planner.engagement(cur, str(enrollment["id"]))

    assert seen["replied"] == 1
    assert seen["has_replied"] is True
    assert seen["no_response"] is False
    assert seen["converted"] == 0


@requires_db
def test_an_open_is_counted_as_an_open(db, tenant):
    """`int(touches.get("opened") or 0)` had its `or` mutated to `and`, which
    makes `opened` zero whenever there are opens — and the mutation lived.

    The test above seeds a reply and asserts the reply. Nothing asserted the
    count this branch is named after, so a play gated on "opened but did not
    reply" would have stopped firing for every contact who opened, silently:
    the step is skipped, the enrollment walks on, and the run reports success.
    """
    tid, _, enrollment = _seed(db, tenant, opened=True)
    with db.tenant_tx(tid) as cur:
        seen = planner.engagement(cur, str(enrollment["id"]))

    assert seen["opened"] == 1, "an open was recorded and counted as none"
    assert seen["replied"] == 0
    assert seen["has_replied"] is False
    assert seen["no_response"] is False, (
        "somebody who opened is not somebody who did not respond")


@requires_db
def test_skipped_steps_are_walked_in_one_write(db, tenant):
    """A sequence whose remaining steps are all excluded would otherwise take
    one tick per step to notice it had finished."""
    tid, program, enrollment = _seed(db, tenant, replied=True)
    with db.tenant_tx(tid) as cur:
        planner.plan_next(cur, tid, enrollment, program)
        cur.execute("select step_index from enrollment where id = %s", (enrollment["id"],))
        assert cur.fetchone()["step_index"] == len(PROGRAM["plays"]["t2"]["steps"])


@requires_db
def test_a_branching_step_lands_inside_the_send_window(db, tenant):
    """Branching and the window compose: the step that survives the condition
    is the one that gets moved."""
    import copy
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    spec = copy.deepcopy(PROGRAM)
    spec["schedule"] = {"send_window": {"days": ["mon"], "opens": "08:00",
                                        "closes": "18:00", "timezone": "Europe/Madrid"}}
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        cur.execute(
            "insert into program (tenant_id, key, version, spec, spec_hash, status)"
            " values (%s,'windowed',1,%s,'h','live') returning *",
            (tid, __import__("json").dumps(spec)))
        program = dict(cur.fetchone())
        cur.execute(
            "insert into enrollment (tenant_id, program_id, entity_type, entity_id,"
            " variant, tier, state, step_index) values (%s,%s,'account',"
            " gen_random_uuid(),'treatment','t2','running',1) returning *",
            (tid, program["id"]))
        enrollment = dict(cur.fetchone())
        planned = planner.plan_next(
            cur, tid, enrollment, program,
            now=datetime(2026, 1, 17, 14, tzinfo=timezone.utc))   # a Saturday

    assert planned.step_key == "email_2"
    landed = planned.scheduled_for.astimezone(ZoneInfo("Europe/Madrid"))
    assert landed.strftime("%a %H:%M") == "Mon 08:00"
