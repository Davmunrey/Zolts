"""Turning an enrollment's position in a play into the next queued action.

The planner is the only writer of the outbox. It queues exactly one step at a
time: queueing a whole sequence up front would commit the runtime to touches
that a reply, an opt-out or a policy change should have cancelled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from runtime import channels
from runtime.engine import triggers
from runtime.repo import actions, enrollments
from zolts import expr, schedule

# Channels the runtime can act on, read from the definitions rather than
# written down again. A step naming anything else is queued as a human task
# rather than silently skipped, because a skipped step in a sequence changes
# the play without anyone deciding to.
#
# This list used to include `linkedin`, `ads` and `webhook`, which have no
# provider behind them: the shipped flagship program's LinkedIn steps queued,
# failed as a permanent error and were cancelled one at a time while the
# sequence carried on. They are somebody's work now, which is what the play
# meant by writing them down.
DISPATCHABLE = channels.dispatchable()


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


def engagement(cur, enrollment_id: str) -> dict[str, Any]:
    """What this contact has done so far, for a step to branch on.

    Read from touches and outcomes rather than kept as a counter: a counter is
    a second source of truth that drifts, and these two tables are already the
    record an audit reads.
    """
    cur.execute(
        "select count(*) filter (where status = 'opened') as opened,"
        " count(*) filter (where status = 'replied') as replied,"
        " count(*) filter (where status = 'bounced') as bounced,"
        " count(*) filter (where status = 'sent') as sent"
        " from touch where enrollment_id = %s", (enrollment_id,))
    touches = cur.fetchone() or {}
    cur.execute("select count(*) as n from outcome where enrollment_id = %s"
                " and type = any(%s)",
                (enrollment_id, ["opp_created", "meeting", "reply_positive"]))
    converted = int((cur.fetchone() or {}).get("n") or 0)

    opened = int(touches.get("opened") or 0)
    replied = int(touches.get("replied") or 0)
    return {
        "opened": opened, "replied": replied,
        "bounced": int(touches.get("bounced") or 0),
        "sent": int(touches.get("sent") or 0),
        "converted": converted,
        # The two a play actually branches on, named so a program reads as
        # English rather than as arithmetic on counters.
        "has_replied": replied > 0,
        "has_opened": opened > 0,
        "no_response": replied == 0 and opened == 0,
    }


def _admits(step: dict[str, Any], scope: dict[str, Any]) -> bool:
    """Whether a step's own condition lets it run.

    A step with no `when` always runs, which is every step written before this
    existed. The same evaluator the triggers and exit rules use: a second
    condition language is a second set of rules to get wrong.
    """
    when = step.get("when")
    if not when:
        return True
    return bool(expr.evaluate(when, scope))


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

    # Walk past steps this contact's behaviour excludes. A breakup email to
    # somebody who already replied is the shape of automation a buyer points at
    # when they say these tools embarrass them.
    scope = {"engagement": engagement(cur, str(enrollment["id"])),
             "tier": enrollment["tier"]}
    skipped = 0
    while index < len(steps) and not _admits(steps[index], scope):
        index += 1
        skipped += 1
    if skipped:
        # Advance past them in one write rather than a tick each: a sequence
        # whose remaining steps are all excluded would otherwise take one tick
        # per step to notice it had finished. Written before the exit below, so
        # a record that says "exited at step 1" is not describing an enrollment
        # that evaluated through step 3.
        enrollments.advance(cur, str(enrollment["id"]), step_index=index,
                            state="running", next_run_at=None)
    if index >= len(steps):
        enrollments.exit_enrollment(cur, str(enrollment["id"]), "sequence_complete")
        return None

    step = steps[index]
    step_key = step.get("step") or f"step_{index}"
    channel = step.get("channel", "task")
    wait = step.get("wait", "0d")
    scheduled = now + (triggers.parse_duration(wait) if wait else timedelta(0))
    # Move the send into the program's declared window. It moves; it never
    # cancels: discarding a step because it fell on a Sunday would throw away
    # work over a scheduling detail. A program with no window is unaffected.
    scheduled = schedule.next_open(scheduled, schedule.window_for(spec))

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


@dataclass(frozen=True)
class Exited:
    """The exit rule that fired, and what the caller still has to do about it.

    `suppress` is returned rather than acted on here because suppression is by
    contact, and this module is given an enrollment. The enrollment names an
    account or a person; which human to stop contacting is a question the
    caller already answered — `inbound` holds the person the reply came from.
    Resolving it a second way here would be a second answer to drift from.
    """
    reason: str
    suppress: bool


def apply_exits(cur, tenant_id: str, enrollment: dict[str, Any], program: dict[str, Any],
                variables: dict[str, Any], now: datetime | None = None) -> Exited | None:
    """Evaluate the program's exit rules. Returns the rule that fired.

    This had no production caller for the whole of the product's life (D-77).
    `spec.exit` is read here and nowhere else, so every shipped programme's
    exit block was declarative content the runtime never evaluated: nothing
    exited on `outcome.type in (...)` and nothing on `days_in_program > 45`.
    It stayed invisible because the two exits that *do* fire cover the ends —
    `sequence_complete` when the steps run out, and `opted_out` from
    `inbound._opt_out` — leaving the product-quality case in between silent.
    A person who booked a meeting kept receiving the rest of the sequence.

    **What is in scope, and why each is here.** `days_in_program` and
    `engagement` are properties of the enrollment and are always bound;
    `engagement` is the same mapping a step's own `when` clause reads, because
    a second vocabulary for the same facts is a second set of rules to get
    wrong. `outcome` is bound by the caller and only when there is one: on the
    tick there is no outcome, and `zolts.expr` answers a comparison against an
    absent field with False. So a rule about an outcome is a non-match on the
    tick rather than an error — the posture D-37 set for a payload that cannot
    answer its own predicate.
    """
    spec = program["spec"]
    days_in = 0
    entered = enrollment.get("entered_at")
    if entered:
        days_in = ((now or datetime.now(timezone.utc)) - entered).days
    scope = {"days_in_program": days_in,
             "engagement": engagement(cur, str(enrollment["id"])),
             **variables}
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
            return Exited(reason=reason, suppress=bool(rule.get("suppress")))
    return None
