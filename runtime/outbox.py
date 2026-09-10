"""Recovering an action that gave up, and retiring one that should not be.

`docs/25`'s outbox runbook told the operator, when a dead action's error is a
`401`, `403` or `invalid_grant`: *the credential expired: re-enter it, then
requeue*. The requeue it supplied was a raw SQL `update` to run by hand against
production, and it is worse than nothing:

    update action set state = 'pending', attempts = 0, run_after = now(),
                      last_error = null
     where state = 'dead' and updated_at > now() - interval '24 hours'
       and channel = 'email';

It sets `last_error = null`, **destroying the only record of why the action
died**, at the moment somebody is deciding what to do about it. It is bulk and
keyed on channel rather than on cause, so it revives every dead email action in
the window whether or not its cause was the one just fixed. It records no
actor and no reason. And it asks an operator in an incident to hand-write an
UPDATE against a live database with no WHERE clause on the thing that matters.

Nothing in the product offered an alternative. `actions.dead_letter` had no
caller. `GET /v1/actions?state=dead` answered and no screen asked. The worklist
ranks a dead action at its second-highest urgency — *nothing will deliver it
and nothing else will notice* — and sent the operator to Programs, which does
not mention them.

**A revive is safe by the mechanism the worker already depends on.**
`runtime/engine/worker` states it: crash safety comes from the lease, and an
action released by an expired lease carries the same idempotency key it had
before, so the provider deduplicates the overlap. At-least-once at the runtime,
exactly-once at the provider. This is that path with a person as the trigger
instead of a timeout — not a new guarantee, the existing one used deliberately.

**One action at a time, and the error is kept.** The bulk statement's appeal
was that it cleared a screen; its cost was that it acted on rows nobody had
looked at. What killed an action is what the operator is deciding about, so it
stays on the row and is copied into the audit entry.

**Both acts carry a written reason**, for the same argument as the sending
switch: the row that says somebody put a failed action back on the wire, or
decided a customer touch was not worth recovering, and not why, is the row
somebody reads back with only it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from runtime.repo import actions, ledger
from zolts.reasons import ReasonRefusal, refuse_reason


class Act(str, Enum):
    REVIVE = "revive"
    DISCARD = "discard"


class Refusal(str, Enum):
    """Why an act was not performed. Never a silent no-op."""

    NO_REASON = ReasonRefusal.NO_REASON.value
    REASON_TOO_SHORT = ReasonRefusal.REASON_TOO_SHORT.value
    REASON_SAYS_NOTHING = ReasonRefusal.REASON_SAYS_NOTHING.value

    NOT_DEAD = "not_dead"
    """No action by that id is dead in this tenant.

    One refusal for four cases — another tenant's action, no action, one still
    pending, one already revived — on purpose: which of them it is, is not
    something an unauthenticated guess should be able to tell apart, and every
    one of them means the same thing to the operator. Their button did not act.
    """


@dataclass(frozen=True)
class Outcome:
    act: Act
    action_id: str
    refusal: Refusal | None = None
    row: dict[str, Any] | None = None

    @property
    def done(self) -> bool:
        return self.refusal is None


def _act(cur, tenant_id: str, action_id: str, *, act: Act, reason: str | None,
         actor: str) -> Outcome:
    refusal = refuse_reason(reason)
    if refusal is not None:
        return Outcome(act, action_id, refusal=Refusal(refusal.value))

    apply = actions.revive if act is Act.REVIVE else actions.discard
    row = apply(cur, action_id)
    if row is None:
        return Outcome(act, action_id, refusal=Refusal.NOT_DEAD)

    text = (reason or "").strip()
    ledger.audit(cur, tenant_id, actor=actor, action=f"outbox.{act.value}d",
                 subject=action_id,
                 detail={"reason": text, "kind": row["kind"],
                         "channel": row["channel"],
                         # What killed it is what the operator decided about,
                         # so it belongs in the row that records the decision
                         # rather than only in the one being changed.
                         "lastError": (row["last_error"] or "")[:300]})
    return Outcome(act, action_id, row=row)


def revive(cur, tenant_id: str, action_id: str, *, reason: str | None,
           actor: str) -> Outcome:
    """Put a dead action back on the wire, with the same idempotency key."""
    return _act(cur, tenant_id, action_id, act=Act.REVIVE, reason=reason,
                actor=actor)


def discard(cur, tenant_id: str, action_id: str, *, reason: str | None,
            actor: str) -> Outcome:
    """Retire a dead action, so the worklist can clear."""
    return _act(cur, tenant_id, action_id, act=Act.DISCARD, reason=reason,
                actor=actor)


def dead(cur, limit: int = 100) -> dict[str, Any]:
    """What gave up, newest first, with what killed it.

    `actions.dead_letter` has answered this since the outbox existed and had no
    caller at all. The error is the whole point of the row: a screen that lists
    dead actions without saying why each died is a list nobody can triage, and
    the runbook's own table groups them by exactly that.
    """
    rows = actions.dead_letter(cur, limit)
    return {
        "count": len(rows),
        "actions": [{
            "id": str(row["id"]),
            "kind": row["kind"],
            "channel": row["channel"],
            "step": row["step_key"],
            "attempts": int(row["attempts"]),
            "maxAttempts": int(row["max_attempts"]),
            "lastError": row["last_error"],
            "diedAt": row["updated_at"].isoformat(),
        } for row in rows],
    }
