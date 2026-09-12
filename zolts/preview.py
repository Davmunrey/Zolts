"""What activating a programme would do today, said before the click.

Activate is the scariest click in the product and the one with no preview: a
programme goes live, its audience is evaluated on the next signal, and the
operator finds out what it did by watching the outbox. `docs/28` OX-2 puts a
number beside the button instead.

This module holds the rules a preview has to obey. It is deliberately not a
computation over the database — `runtime.preview` does that, with the same
functions that will enrol — because the rules are what keep the number honest
and they are what a test can hold without a database.

**A preview is a forecast, stamped and labelled.** It is what the programme
would do *as of now*, from the audience as it stands and the capacity as it
stands. Tomorrow's signal changes it. The stamp is on the answer so nobody
reads yesterday's forecast as today's.

**An audience that cannot be evaluated is reported as that, never as zero.**
Zero enrolments and an audience the runtime could not run look identical on a
tile and are opposite in consequence: one is a quiet programme, the other is
a broken one that will enrol nobody once it is live and say nothing about it.
`runtime.engine.enroll` refuses such an audience at enrolment time; the preview
says so at preview time, in the same words.

**Only the treatment arm is reached.** A holdout is held out. A preview that
counted control-arm contacts as sends would overstate the week by exactly the
holdout, which is the number the measurement is built on.

**Sends in week one are an upper bound.** Every treatment contact reaching
every step whose cumulative wait falls inside seven days, and the label says
*at most*. A preview that guessed at reply-driven exits would be a second
model of the programme, and the day it disagreed with the runtime the operator
would believe the preview.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

WEEK_DAYS = 7

# The priced action each channel spends when a step fires, in addition to the
# step itself. Read from `zolts.billing` by the runtime; named here so the
# mapping is one place rather than a guess per screen.
CHANNEL_ACTION: dict[str, str] = {
    "email": "email.send",
}


class Unanswerable(ValueError):
    """The audience could not be evaluated. The preview is a refusal, not a zero."""


@dataclass(frozen=True)
class StepInWeek:
    step: str
    channel: str | None
    day: int
    """The day the step fires, counted from enrolment. Day 0 is enrolment day."""


def _days(wait: Any) -> int:
    """A step's `wait` as days. `2d` is two days, `12h` rounds down to zero, an
    absent wait is zero. Anything else is refused: a preview that guessed a
    unit would guess the week."""
    if wait in (None, "", 0):
        return 0
    text = str(wait).strip()
    hit = re.fullmatch(r"(\d+)\s*([dh])", text)
    if not hit:
        raise ValueError(f"cannot read wait {wait!r} as days")
    n, unit = int(hit.group(1)), hit.group(2)
    return n if unit == "d" else n // 24


def steps_in_week_one(play: dict[str, Any]) -> list[StepInWeek]:
    """The steps of one play that fire inside the first seven days.

    Waits accumulate: a step with `wait: 2d` after one with `wait: 4d` fires on
    day six, not day two. A step with no channel (a research brief, an agent
    output) fires but sends nothing; it is listed so the operator sees the
    work, and it is priced as a step and not as a send.
    """
    out: list[StepInWeek] = []
    day = 0
    for raw in play.get("steps") or []:
        day += _days(raw.get("wait"))
        if day >= WEEK_DAYS:
            break
        out.append(StepInWeek(step=str(raw.get("step")), channel=raw.get("channel"),
                              day=day))
    return out


def sends_in_week_one(steps: list[StepInWeek], treatment: int) -> int:
    """At most: every treatment contact reaching every sending step."""
    return treatment * sum(1 for s in steps if s.channel)


def credits_in_week_one(steps: list[StepInWeek], treatment: int,
                        prices: dict[str, Decimal]) -> Decimal:
    """Upper bound in credits: a step charge per step per contact, plus the
    channel's priced action where the channel has one. Read against the price
    list the runtime bills from, so the preview and the invoice cannot use two
    prices for one thing."""
    per_contact = Decimal("0")
    for s in steps:
        per_contact += prices["program.step"]
        action = CHANNEL_ACTION.get(s.channel or "")
        if action:
            per_contact += prices[action]
    return per_contact * treatment


@dataclass(frozen=True)
class Preview:
    as_of: str
    audience: int
    control: int
    treatment: int
    headroom: dict[str, int]
    """Per tier: capacity this week minus what has already entered it."""
    steps: list[StepInWeek]
    sends_at_most: int
    credits_at_most: Decimal
    blocked_by_rule: dict[str, int] = field(default_factory=dict)
    """Of a sample of treatment contacts, how many the policy engine refuses
    for the first sending step, by rule key."""
    sampled: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "asOf": self.as_of,
            "answerable": True,
            "audience": self.audience,
            "control": self.control,
            "treatment": self.treatment,
            "headroom": self.headroom,
            "weekOne": {
                "steps": [{"step": s.step, "channel": s.channel, "day": s.day}
                          for s in self.steps],
                "sendsAtMost": self.sends_at_most,
                "creditsAtMost": float(self.credits_at_most),
            },
            "policy": {"sampled": self.sampled,
                       "blockedByRule": dict(self.blocked_by_rule)},
        }


def unanswerable(as_of: str, reason: str) -> dict[str, Any]:
    """The refusal, in the shape of the answer, so a screen renders one or the
    other and never a zero for the second."""
    return {"asOf": as_of, "answerable": False, "reason": reason}
