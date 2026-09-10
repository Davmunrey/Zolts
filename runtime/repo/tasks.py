"""The work items a programme hands to a person, and whether anybody did them.

A step on the `task` or `voice` channel is real work for a human. The runtime
creates it — `worker._dispatch` records a touch with `status = 'queued'` and
`content.awaiting = 'human_review'` — and until now nothing could ever close
it. Every code path that moves a touch off `queued` lives in `inbound.py` and
is driven by a provider event: opened, replied, bounced. A task has no
provider, so no such event arrives (D-83).

That is why `spec.plays.*.steps.sla_hours` could not have been enforced even
if something had read it (D-84): a deadline measured against a state nothing
leaves reports every task as breached for ever, which reads like a service
failure and is a missing transition.

The completion is deliberately not a `status` value. `status` describes what a
*provider* did with a message, and a task has no provider; the fact and its
time go on their own columns, as `complained_at` and `unsubscribed_at` do.
"""

from __future__ import annotations

from typing import Any

from runtime.db import one

#: The channels whose steps are worked by a person rather than sent. A step on
#: one of these is queued and waits; `planner` does not distinguish them, and
#: `worker` decides by the action's kind, so this list is what a *reader* of
#: the queue means by "a task".
HUMAN_CHANNELS = ("task", "voice")


def open_tasks(cur, limit: int = 100) -> list[dict[str, Any]]:
    """Everything waiting for a person, soonest deadline first.

    A task with no declared SLA has no deadline and sorts last: it is still
    work, and it is not late, because nobody said when it was due.
    """
    cur.execute(
        "select t.*, e.entity_id, e.entity_type, p.key as program_key"
        " from touch t"
        " left join enrollment e on e.id = t.enrollment_id"
        " left join program p on p.id = e.program_id"
        " where t.completed_at is null and t.channel = any(%s)"
        "   and t.content->>'awaiting' = 'human_review'"
        " order by t.due_at asc nulls last, t.created_at asc"
        " limit %s",
        (list(HUMAN_CHANNELS), limit))
    return [dict(row) for row in cur.fetchall()]


def overdue(cur, limit: int = 100) -> list[dict[str, Any]]:
    """Open tasks past the deadline their step declared.

    Past due is a property of the task, not of the programme as it stands
    today: the deadline was stamped when the work was created, so republishing
    the programme with a longer SLA does not retroactively make a late task
    punctual.
    """
    cur.execute(
        "select t.*, e.entity_id, e.entity_type, p.key as program_key"
        " from touch t"
        " left join enrollment e on e.id = t.enrollment_id"
        " left join program p on p.id = e.program_id"
        " where t.completed_at is null and t.due_at is not null and t.due_at < now()"
        "   and t.channel = any(%s) and t.content->>'awaiting' = 'human_review'"
        " order by t.due_at asc"
        " limit %s",
        (list(HUMAN_CHANNELS), limit))
    return [dict(row) for row in cur.fetchall()]


def complete(cur, touch_id: str, *, by: str) -> dict[str, Any] | None:
    """Mark one task done. Returns None when it is not one, or already was.

    Once, and only a task. `completed_at is null` in the predicate makes a
    second call a no-op rather than a silent overwrite: the first person to
    say they did the work is the record, and the SLA is judged against when it
    was actually finished rather than when somebody last clicked.
    """
    cur.execute(
        "update touch set completed_at = now(), completed_by = %s"
        " where id = %s and completed_at is null and channel = any(%s)"
        "   and content->>'awaiting' = 'human_review'"
        " returning *",
        (by, touch_id, list(HUMAN_CHANNELS)))
    return one(cur)


def counts(cur) -> dict[str, int]:
    """Open and overdue, for a surface that shows a number before a list."""
    cur.execute(
        "select count(*) filter (where completed_at is null) as open,"
        "       count(*) filter (where completed_at is null and due_at is not null"
        "                          and due_at < now()) as overdue"
        " from touch where channel = any(%s)"
        "   and content->>'awaiting' = 'human_review'",
        (list(HUMAN_CHANNELS),))
    row = cur.fetchone() or {}
    return {"open": int(row.get("open") or 0), "overdue": int(row.get("overdue") or 0)}
