"""Which signals earn their keep: one funnel per signal type, descriptive.

`docs/06` prices signals by tier and gives each a half-life, a freshness SLA
and a legal basis. After a month the operator's first question is *which of
these is worth paying for*, and nothing answered it: the catalogue said what
a signal costs and no screen said what it produced (`docs/28`, OX-3).

This module holds the rule for what the funnel is. The runtime counts the
rows; this decides which rows exist, what each stage counts, when a
conversion is inside the window, and how the rows are ordered.

**Every catalogue signal has a row, including the ones that never fired.** A
signal that fired zero times is the row the operator is paying for and
getting nothing from, and a funnel that showed only the signals with
activity would hide exactly the ones the question is about.

**A conversion counts inside the programme's declared window and nowhere
else.** `opportunity_created_90d` means within ninety days of enrolment
(`zolts.metrics`). An outcome the day before enrolment is not the signal's
doing, and one on day ninety-one is the next period's, not this funnel's. The
window is half-open: the moment of enrolment is inside it, the declared day
is not, which is the edge the frozen report draws.

**Descriptive, not attributed.** The holdout converts too, and this counts
it. Whether the programme *caused* a conversion is the incrementality report's
question (ADR-043), asked per programme, and nothing here pretends to answer
it per signal: `docs/06` SIG-2 stays blocked on exactly that instrument.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping


class FunnelError(ValueError):
    """Columns that disagree with each other. Refused rather than rendered:
    a reader would not know which one lies."""


@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    counts: str
    """What the number is, in the words a reader needs before trusting it."""


STAGES: tuple[Stage, ...] = (
    Stage("fired", "Fired",
          "signals of this type recorded, whatever became of them"),
    Stage("listened", "Listened to by",
          "live programmes whose trigger names this signal today"),
    Stage("enrolled", "Enrolled",
          "enrolments this signal created, both arms, across every programme"),
    Stage("heldOut", "Held out",
          "of those, in the control arm: measured and never reached"),
    Stage("reached", "Reached",
          "treatment enrolments with at least one outbound touch sent"),
    Stage("converted", "Converted",
          "enrolments with an outcome the programme's metric counts, inside the "
          "window it declares, counted from enrolment"),
)


def in_window(entered_at: datetime, occurred_at: datetime, window_days: int) -> bool:
    """Whether an outcome is inside the declared window of an enrolment.

    Half-open: enrolment itself is inside, the declared day is not. An outcome
    before enrolment is outside — the signal cannot have produced it.
    """
    return entered_at <= occurred_at < entered_at + timedelta(days=window_days)


@dataclass(frozen=True)
class Row:
    key: str
    name: str
    tier: str | None
    catalogued: bool
    fired: int = 0
    listened: int = 0
    enrolled: int = 0
    held_out: int = 0
    reached: int = 0
    converted: int = 0
    windows: tuple[int, ...] = ()
    """The declared windows, in days, of the programmes this signal enrolled into."""

    def __post_init__(self) -> None:
        if self.held_out > self.enrolled:
            raise FunnelError(
                f"{self.key}: {self.held_out} held out of {self.enrolled} enrolled")
        if self.reached > self.enrolled - self.held_out:
            raise FunnelError(
                f"{self.key}: {self.reached} reached, and only "
                f"{self.enrolled - self.held_out} were in the arm that is reached")
        if self.converted > self.enrolled:
            raise FunnelError(
                f"{self.key}: {self.converted} converted of {self.enrolled} enrolled")

    @property
    def silent(self) -> bool:
        return self.fired == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "name": self.name, "tier": self.tier,
            "catalogued": self.catalogued,
            "fired": self.fired, "listened": self.listened,
            "enrolled": self.enrolled, "heldOut": self.held_out,
            "reached": self.reached, "converted": self.converted,
            "windows": list(self.windows),
        }


def order(rows: list[Row]) -> list[Row]:
    """Best earner first; among the silent, by key, so the bottom of the list
    is stable enough to be read as a list of what is being paid for."""
    return sorted(rows, key=lambda r: (-r.converted, -r.reached, -r.enrolled,
                                       -r.fired, r.key))


def rows(catalogue: Mapping[str, Any],
         counts: Mapping[str, Mapping[str, Any]]) -> list[Row]:
    """One row per catalogue signal and per signal type that fired.

    `catalogue` maps a key to a definition carrying `name` and `tier`;
    `counts` maps a key to the stage counts the runtime measured. A key in
    the catalogue with no counts is a silent signal and gets a row of zeros.
    A key that fired and is not in the catalogue — a source pushing a type
    nobody defined — gets a row too, marked as such, because money spent on
    an undefined signal is still money.
    """
    keys = set(catalogue) | set(counts)
    out: list[Row] = []
    for key in keys:
        definition = catalogue.get(key)
        c = counts.get(key) or {}
        name = getattr(definition, "name", None) if definition is not None else None
        out.append(Row(
            key=key, name=name or key,
            tier=getattr(definition, "tier", None) if definition is not None else None,
            catalogued=definition is not None,
            fired=int(c.get("fired", 0)), listened=int(c.get("listened", 0)),
            enrolled=int(c.get("enrolled", 0)), held_out=int(c.get("held_out", 0)),
            reached=int(c.get("reached", 0)), converted=int(c.get("converted", 0)),
            windows=tuple(sorted(int(w) for w in (c.get("windows") or ())))))
    return order(out)
