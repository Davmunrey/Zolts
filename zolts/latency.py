"""Time to touch, judged against the tier's target rather than printed raw.

`docs/06` opens by calling signal-to-action latency the highest-leverage
variable in the whole GTM system, publishes a p95 target per signal tier for
three stages, and says of that table: *these are engineering KPIs, not
marketing aspirations: they appear on the customer dashboard and in the
contractual SLA of the Scale and Enterprise plans.*

The runtime measured the latency and compared it to nothing. An operator saw a
number, a tier and no verdict, which is the same as no SLA: a commitment
nobody can fail is not a commitment, and a number nobody can read against a
target is a number that gets worse without anyone noticing.

**The verdict is per signal tier, not per programme** (decision 57). A
programme may consume signals of several tiers, and the tiers are not
alternative descriptions of one deadline — they are different decay curves. A
Tier C signal is a daily batch *by design*, so holding a programme that
consumes one to Tier A's sixty minutes reports a failure on a system behaving
exactly as specified. Judging per tier dissolves the question rather than
answering it, and it is what the document's own table is shaped like: a row
per tier, not a row per programme.

**The targets live here and a test reads `docs/06` to check them** — ADR-017's
mechanism, applied for the fourth time. The test is behavioural: it builds a
p95 at the bound the document names and requires the verdict to flip there,
because a table compared against another table passes on a constant nothing
reads, and that is this repository's dominant defect.

**All three stages have a probe, and the origins do not overlap.** `docs/06`
targets ingestion to signal available, signal to action proposed, and signal to
action executed. The first was unmeasured until `signal.received_at` recorded
when a payload reached this runtime (SIG-1); the other two run from
`signal.ingested_at`, so the trigger evaluation that happens between arrival
and the write is counted once, in the first stage, and never twice.

`received_at` is not the same thing as `observed_at`. The split the console
also publishes — observed to ingested — is how long the *source* took to
notice, upstream of every column in this table. Reporting that against this
target would be a verdict on the wrong quantity, which reads exactly like a
verdict on the right one, so it stays where it is and is labelled for what it
measures.
"""

from __future__ import annotations

from enum import Enum

HOUR = 60
DAY = 24 * HOUR


class Stage(str, Enum):
    """The three columns of `docs/06`'s time-to-touch table."""

    AVAILABLE = "available"
    """A payload arriving to the signal row existing and able to trigger."""

    PROPOSED = "proposed"
    """Signal available to an action proposed for a person to see."""

    EXECUTED = "executed"
    """Signal available to the touch actually sent."""


class Verdict(str, Enum):
    MEETS = "meets"
    MISSES = "misses"
    NO_TARGET = "no_target"
    """The document names something for this cell that is not a p95."""
    NOT_MEASURED = "not_measured"
    """The document names a target and this runtime has no probe for it."""
    NO_DATA = "no_data"
    """Nothing has happened yet, which is not the same as meeting the target."""
    UNKNOWN_TIER = "unknown_tier"
    """A signal whose tier this release does not know. Never a pass."""


# The table in `docs/06` under "Time-to-touch SLA (product commitment)", as
# data, in minutes. `None` where the document names something that is not a
# p95 — Tier C's *daily batch* and Tier D's *per the CS playbook* are
# statements about how the work is scheduled, not deadlines a measurement can
# fail, and inventing a number for them would put a target in the product that
# no document commits to.
TARGETS: dict[str, dict[Stage, int | None]] = {
    "A": {Stage.AVAILABLE: 5,     Stage.PROPOSED: 10,       Stage.EXECUTED: HOUR},
    "B": {Stage.AVAILABLE: HOUR,  Stage.PROPOSED: 2 * HOUR, Stage.EXECUTED: DAY},
    "C": {Stage.AVAILABLE: DAY,   Stage.PROPOSED: None,     Stage.EXECUTED: None},
    "D": {Stage.AVAILABLE: 6 * HOUR, Stage.PROPOSED: 12 * HOUR, Stage.EXECUTED: None},
}

# The stages this runtime can measure. A stage absent here has no probe, and
# `judge` says so rather than returning a pass, or reporting the nearest
# number it happens to hold. The distinction between the two silences matters:
# `NO_TARGET` is the document declining to commit, `NOT_MEASURED` is this
# runtime failing to check something the document does commit to. Collapsing
# them would let a missing probe read as an absent obligation.
MEASURED: frozenset[Stage] = frozenset({Stage.AVAILABLE, Stage.PROPOSED,
                                        Stage.EXECUTED})


def target_minutes(tier: str | None, stage: Stage) -> int | None:
    """The p95 the document commits to, or None where it commits to no p95."""
    return (TARGETS.get(tier or "") or {}).get(stage)


def judge(tier: str | None, stage: Stage, p95_minutes: float | None) -> Verdict:
    """Read one measured p95 against its tier's target.

    The document writes every target as `p95 < x`, so equality misses. That is
    not pedantry: a target written as a strict bound and enforced as a loose
    one is off by exactly the amount somebody will argue about in a renewal.
    """
    if tier not in TARGETS:
        return Verdict.UNKNOWN_TIER
    target = target_minutes(tier, stage)
    if target is None:
        return Verdict.NO_TARGET
    if stage not in MEASURED:
        return Verdict.NOT_MEASURED
    if p95_minutes is None:
        return Verdict.NO_DATA
    return Verdict.MEETS if p95_minutes < target else Verdict.MISSES
