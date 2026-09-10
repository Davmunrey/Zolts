"""The work waiting for a person, on the screen the person uses.

`GET /v1/tasks` has answered this since D-83 gave a human task a way to be
closed at all, and no screen asked it. A queue an operator cannot see is a
queue nobody works, and the SLA `docs/09` gives the task channel is then a
deadline measured against nothing — the same shape D-84 found when
`sla_hours` was read by nothing, one layer up.

Two properties carry the view.

**The deadline is frozen, not recomputed.** It was stamped from the step's own
`sla_hours` when the task was created, so republishing the programme with a
longer SLA does not make a late task punctual. The view reads the stamp.

**Late is computed, never stored.** A stored copy of a comparison is a second
answer waiting to disagree with the two timestamps beside it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from runtime.api import console as console_view
from tests.conftest import requires_db

HUMAN = "human_review"


def _task(cur, tenant_id, *, due_in_hours=None, step="call_revops",
          channel="task", completed=False, awaiting=HUMAN):
    """One queued touch on a human channel, with an explicit deadline.

    Every timestamp is written rather than defaulted, except the completion,
    which the repository stamps. A fixture that lets `now()` supply one end of
    a comparison is a fixture that measures the clock.
    """
    import json

    due = (None if due_in_hours is None
           else datetime.now(timezone.utc) + timedelta(hours=due_in_hours))
    cur.execute(
        "insert into touch (tenant_id, channel, step_key, idempotency_key,"
        " status, content, due_at, direction, completed_at, completed_by)"
        " values (%s,%s,%s,%s,'queued',%s,%s,'out',%s,%s) returning id",
        (tenant_id, channel, step, uuid.uuid4().hex,
         json.dumps({"awaiting": awaiting}), due,
         datetime.now(timezone.utc) if completed else None,
         "key:someone" if completed else None))
    return str(cur.fetchone()["id"])


@requires_db
def test_an_open_task_appears_with_the_deadline_it_was_given(db, tenant):
    with db.tenant_tx(tenant["id"]) as cur:
        _task(cur, tenant["id"], due_in_hours=4)
        view = console_view.tasks_view(cur)
    assert view["counts"]["open"] == 1
    assert view["counts"]["overdue"] == 0
    row = view["tasks"][0]
    assert row["step"] == "call_revops"
    assert row["channel"] == "task"
    assert row["late"] is False
    # Negative is time left. About four hours, allowing for the second the
    # fixture took to insert.
    assert -241 <= row["lateMinutes"] <= -239, row["lateMinutes"]


@requires_db
def test_a_task_past_its_deadline_is_late_and_says_by_how_much(db, tenant):
    with db.tenant_tx(tenant["id"]) as cur:
        _task(cur, tenant["id"], due_in_hours=-3)
        view = console_view.tasks_view(cur)
    row = view["tasks"][0]
    assert row["late"] is True
    assert 179 <= row["lateMinutes"] <= 181, row["lateMinutes"]
    assert view["counts"]["overdue"] == 1


@requires_db
def test_a_task_with_no_declared_sla_is_waiting_and_never_late(db, tenant):
    """Nobody said when it was due, so it cannot be past due.

    Reporting it late would make every task without an SLA permanently
    breached, which is a measurement that never measures — D-76's shape.
    """
    with db.tenant_tx(tenant["id"]) as cur:
        _task(cur, tenant["id"], due_in_hours=None)
        view = console_view.tasks_view(cur)
    row = view["tasks"][0]
    assert row["dueAt"] is None
    assert row["lateMinutes"] is None
    assert row["late"] is False
    assert view["counts"]["overdue"] == 0


@requires_db
def test_a_completed_task_leaves_the_queue(db, tenant):
    with db.tenant_tx(tenant["id"]) as cur:
        _task(cur, tenant["id"], due_in_hours=-9, completed=True)
        view = console_view.tasks_view(cur)
    assert view["tasks"] == []
    assert view["counts"]["open"] == 0
    assert view["counts"]["overdue"] == 0, (
        "a task that is done cannot also be late")


@requires_db
def test_only_work_awaiting_a_person_is_in_the_queue(db, tenant):
    """A queued email is not a human task.

    Every path that moves a touch off `queued` is driven by a provider event.
    An email has a provider and will get one; a task never will, which is the
    whole reason this queue exists. Mixing them would put work in front of an
    operator that nobody is asking them to do.
    """
    with db.tenant_tx(tenant["id"]) as cur:
        _task(cur, tenant["id"], due_in_hours=-1, channel="email",
              awaiting=HUMAN)
        _task(cur, tenant["id"], due_in_hours=-1, awaiting="delivery")
        view = console_view.tasks_view(cur)
    assert view["tasks"] == []


@requires_db
def test_the_soonest_deadline_is_first_and_no_deadline_sorts_last(db, tenant):
    with db.tenant_tx(tenant["id"]) as cur:
        _task(cur, tenant["id"], due_in_hours=None, step="unscheduled")
        _task(cur, tenant["id"], due_in_hours=6, step="later")
        _task(cur, tenant["id"], due_in_hours=-2, step="overdue")
        view = console_view.tasks_view(cur)
    assert [t["step"] for t in view["tasks"]] == ["overdue", "later", "unscheduled"]


@requires_db
def test_the_view_model_carries_the_queue(db, tenant):
    """The screen reads `tasksView`; without it the rail counts nothing."""
    with db.tenant_tx(tenant["id"]) as cur:
        _task(cur, tenant["id"], due_in_hours=-1)
        cur.execute("select * from tenant where id = %s", (tenant["id"],))
        model = console_view.build(cur, dict(cur.fetchone()))
    assert model["tasksView"]["counts"]["open"] == 1
    assert model["tasksView"]["tasks"][0]["late"] is True


def test_the_console_renders_the_queue_and_offers_the_completion():
    """A view model with no screen is the defect this repository keeps finding.

    Read out of the surface rather than rendered here: the browser check drives
    the real thing, clicks Mark done and reads the row back from the database.
    This holds the wiring that check depends on — a rail entry that leads
    somewhere, a dispatcher that reaches the renderer, and a button bound to
    the endpoint that closes the task.
    """
    from pathlib import Path

    surface = (Path(__file__).resolve().parent.parent
               / "design" / "console.html").read_text(encoding="utf-8")
    assert 'data-view="tasks"' in surface, "the rail has no entry for the queue"
    assert 'if (state.view === "tasks"){ renderTasks(); return; }' in surface, (
        "the rail entry leads nowhere")
    assert "function renderTasks()" in surface
    assert '"/v1/tasks/" + b.dataset.id + "/complete"' in surface, (
        "Mark done does not call the endpoint that closes a task")
    assert 'put("nav-tasks"' in surface, "the rail count is never set"
