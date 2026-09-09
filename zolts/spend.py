"""What a tenant's go-to-market cost during one billing period.

`zolts/baseline.py` captures the same four figures once, at onboarding, and
`zolts/report.py` prorates that monthly run-rate across a period to price a
meeting. Nothing re-measures it. A tenant who hires two more people, or drops a
tool, keeps being priced at the figure they gave on their first day — and the
error is invisible, because the report has no other number to disagree with.

Decision 46 registered the correction and named it: capture spend per period
rather than once. This is that declaration. Same four fields, same units and
the same vocabulary as the baseline, so the "before" of `docs/17` and each
period's "after" are one measurement rather than two that share a name.

Pure logic. What is derived is derived here and stored beside its inputs, so a
document a partner signs never moves with a formula.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date

# Deliberately the baseline's own tuple rather than a copy of it. Two lists
# that must agree and are written twice are two lists that will disagree, and
# the whole point of decision 46 is that the before and the after are the same
# measurement.
from zolts.baseline import SPEND_FIELDS


class SpendError(ValueError):
    """A declaration that cannot be recorded as written."""


@dataclass(frozen=True)
class PeriodSpend:
    """One period's declared go-to-market spend, in micros.

    Not prorated and never scaled: the figures are *for this period*, which is
    the assumption the run-rate carried and this exists to remove.
    """
    window_start: date
    window_end: date
    spend_tools_micros: int
    spend_data_micros: int
    spend_sending_micros: int
    spend_people_micros: int

    def __post_init__(self) -> None:
        if self.window_end <= self.window_start:
            raise SpendError("the window ends before it starts")
        for name in SPEND_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise SpendError(f"{name} must be a whole number of micros")
            if value < 0:
                raise SpendError(f"{name} is negative")

    @property
    def total_micros(self) -> int:
        """Everything the tenant spent on go-to-market in the period.

        Zero is a declaration, not an absence: a tenant who ran the period on
        Zolts alone declares four zeros and means them, and the report divides
        by that rather than falling back to a run-rate they have replaced.
        """
        return sum(getattr(self, name) for name in SPEND_FIELDS)

    @property
    def days(self) -> int:
        return (self.window_end - self.window_start).days

    def canonical(self) -> dict[str, object]:
        """The fields the digest covers, in the order and form it covers them."""
        fields = asdict(self)
        fields["window_start"] = self.window_start.isoformat()
        fields["window_end"] = self.window_end.isoformat()
        fields["total_micros"] = self.total_micros
        return dict(sorted(fields.items()))

    def digest(self) -> str:
        """sha256 over the canonical fields, so the row a report divided by and
        the figure an operator declared can be checked against each other."""
        blob = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()


def from_mapping(data: dict[str, object]) -> PeriodSpend:
    """A declaration from the JSON an operator hands the command line."""
    try:
        return PeriodSpend(
            window_start=date.fromisoformat(str(data["window_start"])),
            window_end=date.fromisoformat(str(data["window_end"])),
            **{name: int(data[name]) for name in SPEND_FIELDS})
    except KeyError as exc:
        raise SpendError(f"missing field {exc.args[0]!r}") from exc
    except (TypeError, ValueError) as exc:
        raise SpendError(str(exc)) from exc
