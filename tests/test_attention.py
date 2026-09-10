"""The console opens on the work, not on the inventory.

Nine screens, and they were the same screen nine times: stat tiles, a table, a
detail panel, each answering *what is the state of X* for a different X. None
answered the question an operator arrives with — **what do I do now** — so
answering it meant opening nine screens and joining them by hand. A surface
that makes you do the joining is a surface that does not help you work.

Every judgement this needs was already being made. A draft waiting for a
person, a task past the SLA its step stamped, a mailbox in alarm, a tier
missing the p95 two paid plans commit to, a cost per contact over target, a
programme published and never activated. Each was computed, rendered on its own
screen, and collected by nothing.

Two properties carry the design, and both are tested below.

**Ranked by what ignoring it costs, never by recency.** A newest-first list is
an inbox, and an inbox rewards whoever shouted last. A burning sending domain
outranks a draft waiting for approval because one is reversible and the other
is not.

**Built from the views it summarises, never from its own queries.** A home
screen that counts the review queue a second time is a second answer waiting to
disagree with the first, and the day they diverge the operator believes the
summary. That is how a dashboard starts lying.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from runtime.api import console as console_view
from tests.conftest import requires_db
from zolts import attention
from zolts.attention import ORDER, RANK, Urgency, kind, rank, urgency_counts

EMPTY = {"managed": True, "domains": [], "pausedDomains": []}
NO_QUEUE = {"pending": 0, "dead": 0, "review": 0}
NO_TASKS = {"counts": {"open": 0, "overdue": 0}, "tasks": []}
NO_SIGNALS = {"sla": [], "watched": []}


def _view(**over):
    base = dict(programs=[], queue=dict(NO_QUEUE), fleet_health=dict(EMPTY),
                tasks={"counts": dict(NO_TASKS["counts"])}, spend=None,
                signals=dict(NO_SIGNALS))
    base.update(over)
    return console_view.attention_view(**base)


# -- the ranking rule ----------------------------------------------------


def test_every_kind_declares_what_ignoring_it_costs():
    """A worklist without reasons is a to-do list somebody else wrote."""
    for rule in ORDER:
        assert rule.cost.strip().endswith("."), rule.key
        assert len(rule.cost) > 40, f"{rule.key} does not say what it costs"
        assert rule.view, rule.key
        assert isinstance(rule.urgency, Urgency)


def test_the_irreversible_outranks_the_merely_urgent():
    """A burned domain is not undone by acting tomorrow; a draft is."""
    assert RANK["sending.alarm"] < RANK["review.waiting"]
    assert RANK["outbox.dead"] < RANK["review.waiting"]
    assert RANK["task.overdue"] < RANK["review.waiting"], (
        "a promise already broken outranks one still inside its window")


def test_a_broken_promise_outranks_one_still_inside_its_window():
    assert RANK["task.overdue"] < RANK["task.due"]
    assert kind("task.overdue").urgency is Urgency.NOW
    assert kind("task.due").urgency is Urgency.SOON


def test_margin_ranks_below_an_outage():
    """Cost per contact compounds with volume, not with time."""
    assert RANK["cost.over"] > RANK["sending.alarm"]
    assert RANK["cost.over"] > RANK["sla.missed"]


def test_a_kind_nobody_ranked_is_refused_rather_than_sorted_last():
    """Silently sorting an unknown item to the bottom hides it."""
    with pytest.raises(KeyError, match="not a kind of work"):
        kind("something.new")
    with pytest.raises(KeyError):
        rank([{"kind": "something.new"}])


def test_ranking_is_stable_inside_a_kind():
    """Ties keep the caller's order, which is that view's own ordering.

    The review queue is oldest-first and the task queue is
    soonest-deadline-first. A sort that reshuffled them would make the home
    screen disagree with the screen it links to about which item is first.
    """
    items = [{"kind": "task.due", "n": i} for i in range(5)]
    assert [i["n"] for i in rank(items)] == [0, 1, 2, 3, 4]


def test_the_counts_are_of_the_items_they_summarise():
    counts = urgency_counts([{"kind": "sending.alarm"}, {"kind": "task.due"},
                             {"kind": "review.waiting"}])
    assert counts == {"now": 1, "today": 1, "soon": 1}


# -- what the view raises ------------------------------------------------


def test_nothing_waiting_says_what_the_runtime_is_doing():
    """An empty state that says nothing looks like a screen that failed."""
    view = _view()
    assert view["items"] == []
    assert view["counts"] == {"now": 0, "today": 0, "soon": 0}
    assert set(view["unattended"]) == {"queued", "watching", "live"}


def test_a_mailbox_in_alarm_is_raised_before_a_draft_waiting():
    view = _view(
        queue={"pending": 0, "dead": 0, "review": 3},
        fleet_health={"managed": True, "pausedDomains": [], "domains": [{
            "name": "outbound.example", "health": "alarm",
            "rationale": "bounce rate above 4%",
            "mailboxes": [{"health": "alarm"}]}]})
    kinds = [i["kind"] for i in view["items"]]
    assert kinds == ["sending.alarm", "review.waiting"]
    assert view["items"][0]["urgency"] == "now"
    assert "bounce rate" in view["items"][0]["detail"]


def test_a_paused_domain_is_raised_with_its_reason():
    view = _view(fleet_health={"managed": True, "domains": [], "pausedDomains": [
        {"name": "outbound.example", "pausedReason": "complaint spike"}]})
    assert view["items"][0]["kind"] == "sending.alarm"
    assert "complaint spike" in view["items"][0]["detail"]


def test_overdue_and_open_tasks_are_separate_items():
    """Four open, one late. Three are still inside their window."""
    view = _view(tasks={"counts": {"open": 4, "overdue": 1}})
    by_kind = {i["kind"]: i for i in view["items"]}
    assert by_kind["task.overdue"]["count"] == 1
    assert by_kind["task.due"]["count"] == 3
    assert RANK["task.overdue"] < RANK["task.due"]


def test_a_missed_sla_names_the_stage_that_missed_it():
    view = _view(signals={"watched": [], "sla": [{"tier": "A", "stages": {
        "available": {"verdict": "meets"},
        "proposed": {"verdict": "misses"},
        "executed": {"verdict": "misses"}}}]})
    item = view["items"][0]
    assert item["kind"] == "sla.missed"
    assert item["count"] == 2
    assert "executed" in item["detail"] and "proposed" in item["detail"]


def test_a_met_sla_raises_nothing():
    view = _view(signals={"watched": [], "sla": [{"tier": "A", "stages": {
        "executed": {"verdict": "meets"}, "proposed": {"verdict": "no_data"}}}]})
    assert view["items"] == []


def test_cost_over_target_is_raised_with_both_numbers():
    view = _view(spend={"alerting": False, "costPerContact": {
        "verdict": "misses", "eurPerContact": 0.0512,
        "targetEurPerContact": 0.02}})
    item = view["items"][0]
    assert item["kind"] == "cost.over"
    assert "0.0512" in item["detail"] and "0.02" in item["detail"]


def test_a_programme_never_activated_is_raised_and_named():
    view = _view(programs=[{"key": "series-a", "status": "draft"},
                           {"key": "winback", "status": "live"}])
    item = view["items"][0]
    assert item["kind"] == "program.draft"
    assert item["count"] == 1
    assert "series-a" in item["detail"] and "winback" not in item["detail"]


def test_every_item_carries_a_screen_that_exists():
    """A worklist row that leads nowhere is the dead link D-38 was about."""
    from pathlib import Path

    surface = (Path(__file__).resolve().parent.parent
               / "design" / "console.html").read_text(encoding="utf-8")
    for rule in ORDER:
        assert f'data-view="{rule.view}"' in surface, (
            f"{rule.key} sends the operator to {rule.view!r}, which the rail "
            f"does not have")


# -- against a real database ---------------------------------------------


@requires_db
def test_the_summary_and_the_screen_it_links_to_agree(db, tenant):
    """One query, two readers. The home screen cannot drift from the queue.

    Built by asking `build` for the whole model and comparing the worklist's
    count against the view it summarises, because the failure this guards is
    exactly the two disagreeing.
    """
    with db.tenant_tx(tenant["id"]) as cur:
        cur.execute(
            "insert into touch (tenant_id, channel, step_key, idempotency_key,"
            " status, content, due_at, direction) values (%s,'task','call',%s,"
            " 'queued',%s, now() - interval '2 hours','out')",
            (tenant["id"], uuid.uuid4().hex,
             json.dumps({"awaiting": "human_review"})))
        cur.execute("select * from tenant where id = %s", (tenant["id"],))
        model = console_view.build(cur, dict(cur.fetchone()))

    overdue = model["tasksView"]["counts"]["overdue"]
    assert overdue == 1, "the premise: one task is late"
    raised = [i for i in model["attention"]["items"] if i["kind"] == "task.overdue"]
    assert len(raised) == 1
    assert raised[0]["count"] == overdue, (
        "the worklist and the queue it summarises disagree about how many "
        "tasks are late")


@requires_db
def test_a_fresh_tenant_is_told_to_activate_what_it_was_given(db, tenant):
    """The first thing to do after signup, on the screen that opens first."""
    with db.tenant_tx(tenant["id"]) as cur:
        cur.execute(
            "insert into program (tenant_id, key, version, spec, spec_hash,"
            " status) values (%s,'starter','1.0.0','{}','h','draft')",
            (tenant["id"],))
        cur.execute("select * from tenant where id = %s", (tenant["id"],))
        model = console_view.build(cur, dict(cur.fetchone()))
    items = {i["kind"]: i for i in model["attention"]["items"]}
    assert "program.draft" in items
    assert "presses Activate" in items["program.draft"]["cost"]
