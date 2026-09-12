"""What activating a programme would do today, said before the click.

Activate is the scariest click in the product and the one with no preview
(`docs/28`, OX-2). The rules that keep the number honest are tested here
without a database; the equality that makes it trustworthy — the preview for a
programme equals what activating it enrols in the same second — is tested
against Postgres below, by doing both.

**An audience that cannot be evaluated is reported as that, never as zero.**
Zero enrolments and a broken audience look identical on a tile and are
opposite in consequence.

**Only the treatment arm is reached, and week one is an upper bound.** A
preview that counted the holdout as sends would overstate the week by exactly
the number the measurement is built on.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.conftest import requires_db
from zolts import billing
from zolts.preview import (CHANNEL_ACTION, WEEK_DAYS, StepInWeek, credits_in_week_one,
                           sends_in_week_one, steps_in_week_one, unanswerable)

PLAY = {"steps": [
    {"step": "research_brief", "agent": "researcher"},
    {"step": "task_ae", "channel": "task", "sla_hours": 24},
    {"step": "linkedin_connect", "channel": "linkedin", "wait": "0d"},
    {"step": "email_1", "channel": "email", "wait": "2d"},
    {"step": "exec_touch", "channel": "task", "wait": "4d"},
    {"step": "email_2", "channel": "email", "wait": "3d"},
]}


# -- week one ------------------------------------------------------------


def test_waits_accumulate_and_the_week_ends_on_day_seven():
    """`email_2` waits 3d after a step on day 6: day 9, outside the week.
    Built at the bound: a step landing exactly on day 7 is outside too."""
    steps = steps_in_week_one(PLAY)
    assert [(s.step, s.day) for s in steps] == [
        ("research_brief", 0), ("task_ae", 0), ("linkedin_connect", 0),
        ("email_1", 2), ("exec_touch", 6)]
    at_bound = steps_in_week_one({"steps": [{"step": "a", "channel": "email"},
                                            {"step": "b", "channel": "email",
                                             "wait": f"{WEEK_DAYS}d"}]})
    assert [s.step for s in at_bound] == ["a"]
    inside = steps_in_week_one({"steps": [{"step": "a", "channel": "email"},
                                          {"step": "b", "channel": "email",
                                           "wait": f"{WEEK_DAYS - 1}d"}]})
    assert [s.step for s in inside] == ["a", "b"]


def test_a_wait_in_hours_rounds_down_to_days_and_an_unknown_unit_is_refused():
    assert steps_in_week_one({"steps": [{"step": "a", "wait": "12h"}]})[0].day == 0
    assert steps_in_week_one({"steps": [{"step": "a", "wait": "48h"}]})[0].day == 2
    with pytest.raises(ValueError, match="cannot read wait"):
        steps_in_week_one({"steps": [{"step": "a", "wait": "2 weeks"}]})


def test_only_steps_with_a_channel_send_and_only_treatment_is_reached():
    steps = steps_in_week_one(PLAY)
    assert sends_in_week_one(steps, treatment=10) == 40, (
        "four sending steps times ten contacts; the research brief sends nothing")
    assert sends_in_week_one(steps, treatment=0) == 0


def test_credits_are_priced_from_the_list_the_runtime_bills_from():
    """Every step is a step charge; an email is a send on top. Read against
    `zolts.billing.CREDITS` so the preview and the invoice cannot use two
    prices for one thing."""
    steps = steps_in_week_one(PLAY)
    per_contact = (5 * billing.CREDITS["program.step"]
                   + 1 * billing.CREDITS["email.send"])
    assert credits_in_week_one(steps, 10, billing.CREDITS) == per_contact * 10


def test_the_price_follows_the_list_when_the_list_moves():
    """Behavioural: a constant compared against itself passes on any constant."""
    steps = [StepInWeek("email_1", "email", 0)]
    moved = dict(billing.CREDITS)
    moved["email.send"] = billing.CREDITS["email.send"] + Decimal("3")
    assert credits_in_week_one(steps, 1, moved) == (
        credits_in_week_one(steps, 1, billing.CREDITS) + Decimal("3"))


def test_every_channel_action_the_preview_prices_is_a_priced_action():
    """A channel mapped to an action the price list does not carry would
    raise at preview time on a programme that enrols fine."""
    for action in CHANNEL_ACTION.values():
        assert action in billing.CREDITS, action


# -- the refusal ---------------------------------------------------------


def test_an_unanswerable_audience_is_a_refusal_in_the_shape_of_the_answer():
    """A screen renders one or the other, and never a zero for the second."""
    said = unanswerable("2026-09-11T12:00:00+00:00", "the audience failed: no such column")
    assert said["answerable"] is False
    assert "no such column" in said["reason"]
    assert "audience" not in said or said.get("audience") is None, (
        "a refusal must not carry a count that reads as zero")


# -- against a real database: the preview equals the enrolment ----------

SOFTWARE = ("select id as account_id from account"
            " where industry_code = 'software'")


def _spec(sql: str = SOFTWARE, holdout: int = 20) -> dict:
    return {
        "trigger": {"events": [{"signal": "funding.round"}], "window": "30d"},
        "audience": {"sql": sql},
        "score": {"floor": 0},
        "route": {"tiers": [{"key": "t1", "capacity_per_week": 100}]},
        "plays": {"t1": {"steps": [
            {"step": "research_brief", "agent": "researcher"},
            {"step": "email_1", "channel": "email", "wait": "1d"},
            {"step": "email_2", "channel": "email", "wait": "9d"}]}},
        "experiment": {"holdout_pct": holdout, "unit": "account",
                       "primary_metric": "signed_contract_60d"},
    }


def _live(cur, tenant_id: str, key: str, spec: dict) -> dict:
    from runtime.repo import programs

    published = programs.publish(cur, tenant_id, key=key, version="1.0.0",
                                 spec=spec, spec_hash=f"{key}-hash",
                                 metadata={"name": key})
    programs.activate(cur, str(published["id"]))
    cur.execute("select * from program where id = %s", (published["id"],))
    return dict(cur.fetchone())


def _software_accounts(cur, tenant_id: str, program_key: str, holdout: int,
                       n: int = 12) -> list[str]:
    """`n` software accounts whose ids are fixed, so the holdout split is the
    same on every run and the premise the tests state is a fact, not a hope.

    An id the database generates is random, and `experiment.assign` buckets on
    the id: twelve random accounts at a 20% holdout land every one of them in
    treatment about seven runs in a hundred. A premise that holds ninety-three
    per cent of the time is the ambient-data defect the repository's rule
    names, and this file had it twice. The ids are derived, the arms are read
    off the same function the runtime assigns with, and the set is extended
    from a deterministic spare list until both arms are present.
    """
    import uuid

    from zolts import experiment

    ids = [uuid.uuid5(uuid.NAMESPACE_URL, f"zolts.test/preview/{i}") for i in range(200)]

    def held_out(account_id: uuid.UUID) -> bool:
        return experiment.assign(str(account_id), program_key, holdout, "").is_control

    chosen, spare = ids[:n], ids[n:]
    while not any(held_out(i) for i in chosen):
        chosen[-1] = spare.pop(0)
    while all(held_out(i) for i in chosen):
        chosen[-1] = spare.pop(0)
    assert any(held_out(i) for i in chosen) and not all(held_out(i) for i in chosen), (
        "the premise: both arms are present before the preview is asked about them")
    for k, account_id in enumerate(chosen):
        cur.execute(
            "insert into account (id, tenant_id, name, domain, industry_code)"
            " values (%s,%s,%s,%s,'software')",
            (account_id, tenant_id, f"Soft {k}", f"soft{k}.example"))
    return [str(i) for i in chosen]


@requires_db
def test_the_preview_equals_what_activating_enrols_in_the_same_second(db, tenant):
    """The exit criterion of OX-2: preview, then really enrol every subject,
    and require the same count and the same arm per account. Done by doing
    both, because a preview computed by a second model of the programme is
    a second answer waiting to disagree with the runtime."""
    from datetime import datetime, timezone

    from runtime import preview
    from runtime.engine import enroll
    from runtime.repo import entities

    with db.tenant_tx(tenant["id"]) as cur:
        ids = _software_accounts(cur, tenant["id"], "preview-eq", holdout=20)
        entities.upsert_account(cur, tenant["id"], name="Bricks",
                                domain="bricks.example", industry_code="construction")
        program = _live(cur, tenant["id"], "preview-eq", _spec())
        said = preview.for_program(cur, tenant["id"], program)
        assert said["answerable"] is True
        assert said["audience"] == 12, "the construction account is not software"
        assert said["control"] + said["treatment"] == 12

        arms = {}
        for account in ids:
            result = enroll.ingest(
                cur, tenant["id"], entity_type="account", entity_id=account,
                type="funding.round", strength=0.9, half_life_h=72, source="test",
                legal_basis="legitimate_interest", payload={},
                observed_at=datetime.now(timezone.utc))
            mine = [e for e in result.enrollments if e.program_key == "preview-eq"]
            assert len(mine) == 1, account
            arms[account] = mine[0].variant

    assert sum(1 for v in arms.values() if v == "control") == said["control"]
    assert sum(1 for v in arms.values() if v == "treatment") == said["treatment"]
    assert said["control"] >= 1 and said["treatment"] >= 1, (
        "both arms were established by the fixture, so the equality above "
        "compared two real numbers rather than two zeros")


@requires_db
def test_week_one_is_the_treatment_arm_through_the_steps_inside_seven_days(db, tenant):
    from runtime import preview

    with db.tenant_tx(tenant["id"]) as cur:
        _software_accounts(cur, tenant["id"], "preview-week", holdout=20)
        program = _live(cur, tenant["id"], "preview-week", _spec(holdout=20))
        said = preview.for_program(cur, tenant["id"], program)
    assert said["treatment"] + said["control"] == 12
    assert said["control"] >= 1, "the fixture established that somebody is held out"
    steps = [s["step"] for s in said["weekOne"]["steps"]]
    assert steps == ["research_brief", "email_1"], "email_2 fires on day ten"
    assert said["weekOne"]["sendsAtMost"] == said["treatment"], (
        "one sending step: the holdout is held out, the rest are reached once")
    assert said["headroom"] == {"t1": 100}
    assert said["tier"] == "t1"


@requires_db
def test_an_audience_that_cannot_run_is_refused_not_reported_as_zero(db, tenant):
    """Zero enrolments and a broken audience look identical on a tile and are
    opposite in consequence. Admission refuses this audience at publish time,
    so it is stored directly, as a programme published before the check
    existed would have been."""
    import json

    from runtime import preview

    with db.tenant_tx(tenant["id"]) as cur:
        spec = _spec("select id as account_id from account where no_such_column = 1")
        cur.execute(
            "insert into program (tenant_id, key, version, spec, spec_hash, status)"
            " values (%s,'preview-broken','1.0.0',%s,'h','live') returning *",
            (tenant["id"], json.dumps(spec)))
        program = dict(cur.fetchone())
        said = preview.for_program(cur, tenant["id"], program)
    assert said["answerable"] is False
    assert "no_such_column" in said["reason"]
    assert "audience" not in said


@requires_db
def test_capacity_already_used_this_week_comes_off_the_headroom(db, tenant):
    from datetime import datetime, timezone

    from runtime import preview
    from runtime.engine import enroll
    from runtime.repo import entities

    with db.tenant_tx(tenant["id"]) as cur:
        a = entities.upsert_account(cur, tenant["id"], name="Used",
                                    domain="used.example", industry_code="software")
        program = _live(cur, tenant["id"], "preview-cap", _spec(holdout=10))
        enroll.ingest(cur, tenant["id"], entity_type="account", entity_id=str(a["id"]),
                      type="funding.round", strength=0.9, half_life_h=72, source="test",
                      legal_basis="legitimate_interest", payload={},
                      observed_at=datetime.now(timezone.utc))
        said = preview.for_program(cur, tenant["id"], program)
    assert said["headroom"] == {"t1": 99}, "one account entered t1 this week"
