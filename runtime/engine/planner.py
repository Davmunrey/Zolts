"""Turning an enrollment's position in a play into the next queued action.

The planner is the only writer of the outbox. It queues exactly one step at a
time: queueing a whole sequence up front would commit the runtime to touches
that a reply, an opt-out or a policy change should have cancelled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from runtime.engine import triggers
from runtime.repo import actions, enrollments
from zolts import expr

# Channels the runtime can act on. A step naming anything else is queued as a
# human task rather than silently skipped, because a skipped step in a
# sequence changes the play without anyone deciding to.
DISPATCHABLE = {"email", "linkedin", "task", "ads", "webhook", "crm"}


@dataclass(frozen=True)
class Planned:
    action_id: str | None
    step_key: str
    channel: str
    scheduled_for: datetime
    queued: bool


def steps_for(spec: dict[str, Any], tier: str | None) -> list[dict[str, Any]]:
    plays = spec.get("plays") or {}
    play = plays.get(tier or "", {})
    return list(play.get("steps") or [])


def play_of(spec: dict[str, Any], tier: str | None) -> dict[str, Any]:
    return (spec.get("plays") or {}).get(tier or "", {})


def requires_human(spec: dict[str, Any], tier: str | None) -> bool:
    """True when the play does not permit unattended sending.

    Absent `auto_send` means human review. The default is the safe one: a
    program that forgets the key must not start sending on its own.
    """
    return not bool(play_of(spec, tier).get("auto_send", False))


def idempotency_key(program_key: str, enrollment_id: str, step_key: str) -> str:
    """Stable across retries, unique across steps.

    The enrollment id rather than the entity id, so a re-enrollment after a
    cooldown is a genuinely new sequence rather than a permanent duplicate.
    """
    return f"{program_key}/{enrollment_id}/{step_key}"


def plan_next(cur, tenant_id: str, enrollment: dict[str, Any],
              program: dict[str, Any], now: datetime | None = None) -> Planned | None:
    """Queue the enrollment's next step, or return None when the play is done.

    Returns None with the enrollment exited when there are no steps left.
    """
    now = now or datetime.now(timezone.utc)
    if enrollment["variant"] == "control":
        # Product invariant: the holdout is never touched. This is a hard stop,
        # not a filter that a future caller could pass around.
        return None

    spec = program["spec"]
    steps = steps_for(spec, enrollment["tier"])
    index = int(enrollment["step_index"])
    if index >= len(steps):
        enrollments.exit_enrollment(cur, str(enrollment["id"]), "sequence_complete")
        return None

    step = steps[index]
    step_key = step.get("step") or f"step_{index}"
    channel = step.get("channel", "task")
    wait = step.get("wait", "0d")
    scheduled = now + (triggers.parse_duration(wait) if wait else timedelta(0))

    key = idempotency_key(program["key"], str(enrollment["id"]), step_key)
    payload = {
        "step": step,
        "tier": enrollment["tier"],
        "entity_type": enrollment["entity_type"],
        "entity_id": str(enrollment["entity_id"]),
        "requires_human": requires_human(spec, enrollment["tier"]),
        "auto_send_requires": play_of(spec, enrollment["tier"]).get("auto_send_requires", {}),
        "agent": step.get("agent"),
    }
    # A step naming an agent is generated before it is sent. The agent writes a
    # proposal; only the gate can turn one into a dispatch. Queueing it as a
    # dispatch would let generated text reach a provider without passing the
    # eval gate at all.
    if step.get("agent"):
        kind = "generate"
    elif channel in DISPATCHABLE:
        kind = "dispatch"
    else:
        kind = "manual"
    row = actions.enqueue(
        cur, tenant_id, kind=kind,
        idempotency_key=key, payload=payload, enrollment_id=str(enrollment["id"]),
        program_id=str(program["id"]), channel=channel, step_key=step_key,
        run_after=scheduled)

    # The enrollment advances whether or not the action was newly queued. A
    # duplicate key means this step is already in flight, and re-queueing it on
    # the next tick would spin forever on the same index.
    enrollments.advance(cur, str(enrollment["id"]), step_index=index + 1,
                        state="running", next_run_at=scheduled + timedelta(seconds=1))
    return Planned(str(row["id"]) if row else None, step_key, channel, scheduled, row is not None)


def apply_exits(cur, tenant_id: str, enrollment: dict[str, Any], program: dict[str, Any],
                variables: dict[str, Any]) -> str | None:
    """Evaluate the program's exit rules. Returns the reason when one fires."""
    spec = program["spec"]
    days_in = 0
    entered = enrollment.get("entered_at")
    if entered:
        days_in = (datetime.now(timezone.utc) - entered).days
    scope = {"days_in_program": days_in, **variables}
    for rule in spec.get("exit") or []:
        when = rule.get("when")
        if not when:
            continue
        if expr.evaluate(when, scope):
            reason = rule.get("reason", "exited")
            enrollments.exit_enrollment(cur, str(enrollment["id"]), reason)
            # Cancel work already queued for a sequence that has ended. Without
            # this, an opt-out recorded today still sends tomorrow's step.
            cur.execute(
                "update action set state = 'cancelled', last_error = %s, updated_at = now()"
                " where enrollment_id = %s and state in ('pending','leased')",
                (f"enrollment exited: {reason}", str(enrollment["id"])))
            return reason
    return None
