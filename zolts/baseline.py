"""The baseline: what a tenant's GTM cost and produced before Zolts.

`docs/17` calls capturing it the irreversible phase-1 requirement, `docs/13`
makes it an exit criterion and `docs/14` a KPI — and nothing stored one
(D-41). It cannot be reconstructed later: the month-9 conversation with the
CFO compares after with before, and "before" is only ever available at the
start.

Pure logic. What is derived from the declared figures is derived here, once,
and stored beside them, so the document a partner signs never changes with a
formula. The digest is over the canonical fields, so anybody holding the
signed letter can recompute it against the row.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date

# Declared at onboarding: four fields, EUR per month, in micros. Four because
# `docs/17` says four and a form with more is a form nobody finishes.
SPEND_FIELDS = ("spend_tools_micros", "spend_data_micros", "spend_sending_micros",
                "spend_people_micros")

# The window is the prior ninety days (`docs/13`), so monthly spend is scaled
# by this to sit beside the window's outcomes.
WINDOW_MONTHS = 3

SOURCES = ("declared", "crm")


class BaselineError(ValueError):
    """A baseline that cannot be frozen as written."""


@dataclass(frozen=True)
class Baseline:
    """Ninety days before Zolts, as declared or as read from the CRM."""
    window_start: date
    window_end: date
    spend_tools_micros: int
    spend_data_micros: int
    spend_sending_micros: int
    spend_people_micros: int
    contacted: int
    replied: int
    meetings: int
    opportunities: int
    source: str = "declared"

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise BaselineError(f"source must be one of {SOURCES}, not {self.source!r}")
        if self.window_end <= self.window_start:
            raise BaselineError("the window ends before it starts")
        for name in SPEND_FIELDS + ("contacted", "replied", "meetings", "opportunities"):
            if getattr(self, name) < 0:
                raise BaselineError(f"{name} is negative")
        # A funnel that widens is a typo, and a signed typo is worse than none.
        if not (self.contacted >= self.replied >= 0 and self.contacted >= self.meetings):
            raise BaselineError("replies and meetings cannot exceed the contacts made")

    @property
    def monthly_spend_micros(self) -> int:
        return sum(getattr(self, name) for name in SPEND_FIELDS)

    @property
    def window_spend_micros(self) -> int:
        return self.monthly_spend_micros * WINDOW_MONTHS

    @property
    def cost_per_meeting_micros(self) -> int | None:
        """None, not zero, when there were no meetings: the number does not
        exist, and a zero would read as free."""
        return self.window_spend_micros // self.meetings if self.meetings else None

    @property
    def cost_per_opportunity_micros(self) -> int | None:
        return (self.window_spend_micros // self.opportunities
                if self.opportunities else None)

    @property
    def reply_rate(self) -> float | None:
        return self.replied / self.contacted if self.contacted else None

    def canonical(self) -> dict[str, object]:
        """The fields the digest covers, in the order and form it covers them."""
        fields = asdict(self)
        fields["window_start"] = self.window_start.isoformat()
        fields["window_end"] = self.window_end.isoformat()
        fields["cost_per_meeting_micros"] = self.cost_per_meeting_micros
        fields["cost_per_opportunity_micros"] = self.cost_per_opportunity_micros
        return dict(sorted(fields.items()))

    def digest(self) -> str:
        """sha256 over the canonical fields. Quoted in the signed letter, so the
        row and the letter can be checked against each other by anyone."""
        blob = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()


def from_mapping(data: dict[str, object]) -> Baseline:
    """A baseline from a JSON body. Dates as ISO strings; everything else as it is."""
    try:
        return Baseline(
            window_start=date.fromisoformat(str(data["window_start"])),
            window_end=date.fromisoformat(str(data["window_end"])),
            spend_tools_micros=int(data["spend_tools_micros"]),
            spend_data_micros=int(data["spend_data_micros"]),
            spend_sending_micros=int(data["spend_sending_micros"]),
            spend_people_micros=int(data["spend_people_micros"]),
            contacted=int(data["contacted"]),
            replied=int(data["replied"]),
            meetings=int(data["meetings"]),
            opportunities=int(data["opportunities"]),
            source=str(data.get("source") or "declared"),
        )
    except KeyError as exc:
        raise BaselineError(f"missing field {exc.args[0]!r}") from exc
    except (TypeError, ValueError) as exc:
        raise BaselineError(str(exc)) from exc
