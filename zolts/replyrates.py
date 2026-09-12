"""Which copy works: reply and positive-reply rates per step, with the sample
beside the rate, and no rate under the floor the experiment accepts.

Every sequencing tool shows an open rate. None shows a rate with the sample
size that makes it believable, and none withholds the rate when the sample
cannot carry one (`docs/28`, OX-4). `zolts.experiment` already draws that
line for the programme's lift: below `MIN_CONVERSIONS_PER_ARM` conversions
the baseline is a guess and a rate computed from a guess is a number that
looks precise and is not. The same floor applies here, for the same reason,
and it is read from the same constant so the two cannot drift apart.

**A withheld rate says why.** Three positive replies out of sixty is shown
as *3 of 60, no rate: 3 of 5 needed*, never as 5.0% and never as a
dash. The count is real; the rate is not yet.

**A reply is credited to the step it answered.** The runtime records a
positive reply on the enrolment, not on a step; the step is the last one
sent to that enrolment before the reply arrived. The credit rule lives in the
reader's query and is guarded there; this module says what the row is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from zolts.experiment import MIN_CONVERSIONS_PER_ARM

FLOOR = MIN_CONVERSIONS_PER_ARM
TREATMENT = "treatment"
# A step on this channel is work for a person, not copy: nothing was written
# to be replied to.
NOT_COPY = ("task",)


@dataclass(frozen=True)
class Rate:
    count: int
    of: int
    value: float | None
    """A share in [0, 1], or None when the sample cannot carry one."""
    withheld_because: str | None

    def as_dict(self) -> dict[str, Any]:
        return {"count": self.count, "of": self.of,
                "value": None if self.value is None else round(self.value, 4),
                "withheldBecause": self.withheld_because}


def rate(count: int, of: int, floor: int = FLOOR) -> Rate:
    """The share, or the reason there is none.

    The floor is on the numerator: five replies out of five hundred is a
    rate the normal approximation can stand behind; four out of forty is
    not, however tidy ten per cent looks.
    """
    if count < 0 or of < 0 or count > of:
        raise ValueError(f"{count} of {of} is not a count of a sample")
    if of == 0:
        return Rate(count, of, None, "nothing sent")
    if count < floor:
        # Short, because it sits in a panel cell that does not wrap: the
        # sentence behind it — the two-proportion test's floor — is said once,
        # under the block, rather than beside every count.
        return Rate(count, of, None, f"{count} of {floor} needed")
    return Rate(count, of, count / of, None)


@dataclass(frozen=True)
class StepCopy:
    step: str
    variant: str
    sent: int
    replied: int
    positive: int

    def __post_init__(self) -> None:
        if self.replied > self.sent or self.positive > self.sent:
            raise ValueError(
                f"{self.step}/{self.variant}: {self.replied} replied and "
                f"{self.positive} positive out of {self.sent} sent")

    @property
    def reply_rate(self) -> Rate:
        return rate(self.replied, self.sent)

    @property
    def positive_rate(self) -> Rate:
        return rate(self.positive, self.sent)

    def as_dict(self) -> dict[str, Any]:
        return {"step": self.step, "variant": self.variant, "sent": self.sent,
                "replied": self.replied, "positive": self.positive,
                "replyRate": self.reply_rate.as_dict(),
                "positiveRate": self.positive_rate.as_dict()}


def copy_steps(spec: Mapping[str, Any]) -> list[str]:
    """The steps of every play that carry copy, in play order, once each.

    A step appears whether or not it has sent anything yet: a play with four
    emails and one row is a play whose second email has never gone out, and
    that is worth seeing.
    """
    seen: list[str] = []
    for play in (spec.get("plays") or {}).values():
        for step in (play or {}).get("steps") or []:
            key, channel = step.get("step"), step.get("channel")
            if not key or not channel or channel in NOT_COPY or key in seen:
                continue
            seen.append(key)
    return seen


def rows(spec: Mapping[str, Any],
         counts: Mapping[tuple[str, str], Mapping[str, int]]) -> list[StepCopy]:
    """One row per copy step and arm, in play order, the treatment arm first.

    A step the play names and nothing has sent gets a row of zeros. A step
    that sent and the play no longer names — an old version's step — gets a
    row too, after the play's own: the sends happened.
    """
    order = copy_steps(spec)
    extra = sorted({step for step, _ in counts if step not in order})
    out: list[StepCopy] = []
    for step in order + extra:
        arms = sorted({v for s, v in counts if s == step} | {TREATMENT},
                      key=lambda v: (v != TREATMENT, v))
        for variant in arms:
            c = counts.get((step, variant)) or {}
            out.append(StepCopy(step=step, variant=variant,
                                sent=int(c.get("sent", 0)),
                                replied=int(c.get("replied", 0)),
                                positive=int(c.get("positive", 0))))
    return out
