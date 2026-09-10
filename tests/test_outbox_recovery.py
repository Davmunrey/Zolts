"""The requeue the runbook supplied, and what it destroyed.

`docs/25`'s outbox runbook told the operator, when a dead action's error is a
`401`, `403` or `invalid_grant`: *the credential expired: re-enter it, then
requeue* — and the requeue was a raw SQL `update` to run by hand against
production. It sets `last_error = null`, destroying the only record of why the
action died at the moment somebody is deciding what to do about it. It is bulk
and keyed on channel rather than on cause, so it revives every dead action in
the window whether or not its cause was the one just fixed. It records no actor
and no reason.

Nothing in the product offered an alternative. `actions.dead_letter` had no
caller. `GET /v1/actions?state=dead` answered and no screen asked. The worklist
ranks a dead action at its second-highest urgency — *nothing will deliver it
and nothing else will notice* — and sent the operator to Programs, which does
not mention them.

**A revive re-runs with the action's own idempotency key**, which is the
guarantee an expired lease already relies on: at-least-once at the runtime,
exactly-once at the provider. Not a new promise — the existing one, used
deliberately.
"""

from __future__ import annotations

import json
import uuid

import pytest

from runtime import outbox
from runtime.api import console as console_view
from runtime.repo import actions
from tests.conftest import requires_db
from zolts.reasons import MIN_REASON, ReasonRefusal, refuse_reason

RUNBOOK_ERROR = "401 the provider refused the credential: invalid_grant"


def _action(cur, tenant_id, *, state="dead", attempts=5, error=RUNBOOK_ERROR,
            kind="send", channel="email"):
    """One action in a named state, with everything written rather than
    defaulted. A fixture that lets a default supply the state under test is a
    fixture that tests the default."""
    cur.execute(
        "insert into action (tenant_id, kind, channel, step_key,"
        " idempotency_key, payload, state, attempts, max_attempts, last_error)"
        " values (%s,%s,%s,'email_1',%s,%s,%s,%s,5,%s) returning *",
        (tenant_id, kind, channel, uuid.uuid4().hex, json.dumps({"step": {}}),
         state, attempts, error))
    return dict(cur.fetchone())


# -- the shared reason rule ----------------------------------------------


def test_a_reason_that_restates_the_act_is_refused_here_too():
    """The rule moved to `zolts.reasons` the moment a second act needed it.

    "Requeue" and "retry" restate this act exactly as "fixed" restated the
    sending one, so they are on the same list.
    """
    assert refuse_reason("requeue retry again") is ReasonRefusal.REASON_SAYS_NOTHING
    assert refuse_reason("the token was rotated this morning") is None


def test_the_length_bound_is_behavioural():
    at_bound = "c" * MIN_REASON
    assert refuse_reason(at_bound) is None
    assert refuse_reason(at_bound[:-1]) is ReasonRefusal.REASON_TOO_SHORT


# -- reviving ------------------------------------------------------------


@requires_db
def test_a_dead_action_goes_back_on_the_wire_with_the_same_key(db, tenant):
    """The property the whole act rests on. A revive that minted a new
    idempotency key would be a second send with nothing to deduplicate it
    against, which is the one thing invariant 2 exists to prevent."""
    with db.tenant_tx(tenant["id"]) as cur:
        before = _action(cur, tenant["id"])
        result = outbox.revive(cur, tenant["id"], str(before["id"]),
                               reason="the token was rotated this morning",
                               actor="key:someone")
        assert result.done
        cur.execute("select * from action where id = %s", (before["id"],))
        after = dict(cur.fetchone())
    assert after["state"] == "pending"
    assert after["idempotency_key"] == before["idempotency_key"], (
        "a revived action carries a new key, so the provider has nothing to "
        "deduplicate it against")


@requires_db
def test_the_attempt_budget_is_restored(db, tenant):
    """Leaving it at the maximum makes the action die again on the first
    hiccup, which is a requeue that requeues nothing."""
    with db.tenant_tx(tenant["id"]) as cur:
        row = _action(cur, tenant["id"], attempts=5)
        outbox.revive(cur, tenant["id"], str(row["id"]),
                      reason="the token was rotated this morning",
                      actor="key:someone")
        cur.execute("select attempts, run_after <= now() as due"
                    " from action where id = %s", (row["id"],))
        after = dict(cur.fetchone())
    assert after["attempts"] == 0
    assert after["due"] is True, "revived and then not due for six hours"


@requires_db
def test_what_killed_it_is_recorded_beside_the_decision(db, tenant):
    with db.tenant_tx(tenant["id"]) as cur:
        row = _action(cur, tenant["id"])
        outbox.revive(cur, tenant["id"], str(row["id"]),
                      reason="the token was rotated this morning",
                      actor="key:someone")
        cur.execute("select * from audit_log where action = 'outbox.revived'")
        entry = dict(cur.fetchone())
    assert entry["actor"] == "key:someone"
    assert "rotated" in entry["detail"]["reason"]
    assert "invalid_grant" in entry["detail"]["lastError"], (
        "the row records the decision and not what it was about")


@requires_db
def test_a_revive_with_no_reason_leaves_the_action_dead(db, tenant):
    with db.tenant_tx(tenant["id"]) as cur:
        row = _action(cur, tenant["id"])
        result = outbox.revive(cur, tenant["id"], str(row["id"]), reason=" ",
                               actor="key:someone")
        assert result.refusal is outbox.Refusal.NO_REASON
        cur.execute("select state from action where id = %s", (row["id"],))
        assert cur.fetchone()["state"] == "dead"
        cur.execute("select count(*) as n from audit_log")
        assert cur.fetchone()["n"] == 0


@requires_db
@pytest.mark.parametrize("state", ["pending", "succeeded", "cancelled"])
def test_only_a_dead_action_can_be_revived(db, tenant, state):
    """A pending action is already coming and a succeeded one is done.

    Reviving either is a second send with no failure behind it, and reviving a
    cancelled one is retrying a policy decision — for a suppression, an attempt
    to contact somebody who asked not to be contacted.
    """
    with db.tenant_tx(tenant["id"]) as cur:
        row = _action(cur, tenant["id"], state=state)
        result = outbox.revive(cur, tenant["id"], str(row["id"]),
                               reason="trying to revive the wrong thing",
                               actor="key:someone")
        assert result.refusal is outbox.Refusal.NOT_DEAD
        cur.execute("select state from action where id = %s", (row["id"],))
        assert cur.fetchone()["state"] == state


# -- discarding ----------------------------------------------------------


@requires_db
def test_a_discard_is_its_own_state_not_a_policy_cancellation(db, tenant):
    """`cancelled` is the runtime's judgement and carries the rule that made
    it. A discard is a person's. Conflating them would put an operator's
    abandonment in the same bucket as a suppression, and the Policy view would
    report a decision nobody made."""
    with db.tenant_tx(tenant["id"]) as cur:
        row = _action(cur, tenant["id"])
        result = outbox.discard(cur, tenant["id"], str(row["id"]),
                                reason="the account was closed last week",
                                actor="key:someone")
        assert result.done
        cur.execute("select state, policy_decision_id from action where id = %s",
                    (row["id"],))
        after = dict(cur.fetchone())
    assert after["state"] == "discarded"
    assert after["policy_decision_id"] is None


@requires_db
def test_a_discarded_action_leaves_the_worklist(db, tenant):
    """Without this a dead row nobody will act on is a permanent alarm, and a
    permanent alarm is one the operator learns to scroll past."""
    with db.tenant_tx(tenant["id"]) as cur:
        row = _action(cur, tenant["id"])
        assert outbox.dead(cur)["count"] == 1, "the premise: one is dead"
        outbox.discard(cur, tenant["id"], str(row["id"]),
                       reason="the account was closed last week",
                       actor="key:someone")
        assert outbox.dead(cur)["count"] == 0


# -- the view, and the screen --------------------------------------------


@requires_db
def test_the_view_says_what_died_and_why(db, tenant):
    """A list of dead actions without the error is a list nobody can triage,
    and the runbook's own table groups them by exactly that."""
    with db.tenant_tx(tenant["id"]) as cur:
        _action(cur, tenant["id"])
        view = outbox.dead(cur)
    row = view["actions"][0]
    assert row["kind"] == "send" and row["channel"] == "email"
    assert row["attempts"] == 5 and row["maxAttempts"] == 5
    assert "invalid_grant" in row["lastError"]


@requires_db
def test_the_worklist_and_the_outbox_screen_agree(db, tenant):
    """One query, two readers. The count the home screen raises is the count
    the screen it links to renders."""
    with db.tenant_tx(tenant["id"]) as cur:
        _action(cur, tenant["id"])
        _action(cur, tenant["id"], channel="linkedin")
        cur.execute("select * from tenant where id = %s", (tenant["id"],))
        model = console_view.build(cur, dict(cur.fetchone()))
    raised = [i for i in model["attention"]["items"] if i["kind"] == "outbox.dead"]
    assert len(raised) == 1
    assert raised[0]["count"] == model["outboxView"]["count"] == 2


def test_the_worklist_row_leads_to_the_screen_that_owns_the_action():
    """It pointed at Programs, which does not mention a dead action.

    A row that sends the operator somewhere the work cannot be done is the dead
    link this console keeps having to remove.
    """
    from zolts.attention import kind

    assert kind("outbox.dead").view == "outbox"


def test_the_console_renders_the_outbox_and_calls_both_endpoints():
    from pathlib import Path

    surface = (Path(__file__).resolve().parent.parent
               / "design" / "console.html").read_text(encoding="utf-8")
    assert 'data-view="outbox"' in surface, "the rail has no entry for the outbox"
    assert 'if (state.view === "outbox"){ renderOutbox(); return; }' in surface, (
        "the rail entry leads nowhere")
    assert "function renderOutbox()" in surface
    assert '"/v1/outbox/" + encodeURIComponent(b.dataset.id) + "/" + pair[1]' in surface
    assert 'put("nav-outbox"' in surface, "the rail count is never set"
    for refusal in ("no_reason", "reason_too_short", "reason_says_nothing",
                    "not_dead"):
        assert refusal + ":" in surface, (
            f"the screen cannot name the {refusal!r} refusal")


def test_the_screen_reads_the_runbooks_own_triage():
    """`docs/25` keys the operator's next step off the error text. The screen
    says the same thing, so the guidance in front of the operator and the
    guidance in the runbook cannot drift apart."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    runbook = (root / "docs" / "25-runbook.md").read_text(encoding="utf-8")
    surface = (root / "design" / "console.html").read_text(encoding="utf-8")
    for token in ("401", "403", "invalid_grant", "429"):
        assert token in runbook, f"the runbook no longer triages on {token}"
        assert token in surface, (
            f"the runbook triages on {token} and the screen does not")
