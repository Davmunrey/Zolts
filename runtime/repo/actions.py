"""The transactional outbox.

`enqueue` is written in the same transaction as the state change that justifies
the action. Nothing else in the runtime may talk to a provider, so an action
that is not in this table cannot happen, and an action in this table happens at
least once.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from runtime.db import one, rows

# Retry schedule in seconds, indexed by attempt. Deliberately short at the start
# (a provider blip clears in seconds) and long at the end (a credential problem
# needs a human, and hammering it burns rate limit that live traffic needs).
BACKOFF_SECONDS = [30, 120, 600, 3600, 21600]


def enqueue(cur, tenant_id: str, *, kind: str, idempotency_key: str,
            payload: dict[str, Any], enrollment_id: str | None = None,
            program_id: str | None = None, channel: str | None = None,
            step_key: str | None = None, run_after: datetime | None = None,
            max_attempts: int = 5) -> dict[str, Any] | None:
    """Queue one action. Returns None when this idempotency key is already queued."""
    cur.execute(
        "insert into action (tenant_id, enrollment_id, program_id, kind, channel,"
        " step_key, idempotency_key, payload, run_after, max_attempts)"
        " values (%s,%s,%s,%s,%s,%s,%s,%s,coalesce(%s, now()),%s)"
        " on conflict (tenant_id, idempotency_key) do nothing returning *",
        (tenant_id, enrollment_id, program_id, kind, channel, step_key,
         idempotency_key, json.dumps(payload), run_after, max_attempts),
    )
    return one(cur)


def get(cur, action_id: str) -> dict[str, Any] | None:
    cur.execute("select * from action where id = %s", (action_id,))
    return one(cur)


def succeed(cur, action_id: str, result: dict[str, Any],
            policy_decision_id: str | None = None) -> None:
    cur.execute(
        "update action set state = 'succeeded', result = %s, last_error = null,"
        " policy_decision_id = coalesce(%s, policy_decision_id), leased_until = null,"
        " updated_at = now() where id = %s",
        (json.dumps(result), policy_decision_id, action_id),
    )


def cancel(cur, action_id: str, reason: str, policy_decision_id: str | None = None) -> None:
    """Terminal, non-retryable. A denied action is not a failed action.

    Retrying a policy denial would be both useless and, for a suppression, an
    attempt to send to someone who asked not to be contacted.
    """
    cur.execute(
        "update action set state = 'cancelled', last_error = %s,"
        " policy_decision_id = coalesce(%s, policy_decision_id), leased_until = null,"
        " updated_at = now() where id = %s",
        (reason, policy_decision_id, action_id),
    )


def defer(cur, action_id: str, reason: str, until: datetime | None = None) -> None:
    """Hold an action without consuming an attempt.

    A tenant who has spent their credits has not done anything wrong and their
    work is not a failure: cancelling would discard it, and failing would burn
    the retry budget on a condition no retry can change. It goes back to
    pending, due when the period turns.
    """
    cur.execute(
        "update action set state = 'pending', last_error = %s,"
        " run_after = coalesce(%s, date_trunc('month', now()) + interval '1 month'),"
        " leased_until = null, lease_owner = null, updated_at = now()"
        " where id = %s",
        (reason, until, action_id),
    )


def fail(cur, action_id: str, error: str) -> str:
    """Record a failure and schedule the retry, or bury the action.

    Returns the resulting state so the caller can log the transition without a
    second read.
    """
    cur.execute("select attempts, max_attempts from action where id = %s", (action_id,))
    row = one(cur)
    if row is None:
        raise LookupError(f"action {action_id} not found in this tenant")
    attempts, max_attempts = int(row["attempts"]), int(row["max_attempts"])
    if attempts >= max_attempts:
        cur.execute(
            "update action set state = 'dead', last_error = %s, leased_until = null,"
            " updated_at = now() where id = %s",
            (error[:2000], action_id),
        )
        return "dead"
    delay = BACKOFF_SECONDS[min(attempts, len(BACKOFF_SECONDS)) - 1]
    cur.execute(
        "update action set state = 'pending', last_error = %s, leased_until = null,"
        " run_after = now() + make_interval(secs => %s), updated_at = now() where id = %s",
        (error[:2000], delay, action_id),
    )
    return "pending"


def pending_count(cur) -> int:
    cur.execute("select count(*) as n from action where state in ('pending','leased')")
    return int(one(cur)["n"])


def by_state(cur, state: str, limit: int = 100) -> list[dict[str, Any]]:
    cur.execute(
        "select * from action where state = %s order by updated_at desc limit %s",
        (state, limit),
    )
    return rows(cur)


def dead_letter(cur, limit: int = 100) -> list[dict[str, Any]]:
    return by_state(cur, "dead", limit)
