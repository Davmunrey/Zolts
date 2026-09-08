"""What a program's declared primary metric counts, and inside what window.

Every program declares `experiment.primary_metric` — the schema requires it,
the console displays it, and nothing measured by it (D-51). Four shipped
programs declare four different metrics and all four were measured the same
way: any of `opp_created`, `meeting` or `reply_positive`, at any time after
enrolment. So a PLG program declaring `paid_conversion_30d` was judged on
positive replies, and a metric with a ninety-day window counted a conversion
from month six.

Two things a metric name carries, and both change the answer:

* **What counts.** A positive reply is not a signed contract, and a program
  that says it is measuring contracts is measuring contracts or it is
  measuring nothing (`docs/10`: the metric is frozen when the version is
  published).
* **When it stops counting.** `opportunity_created_90d` means within ninety
  days of that account entering the program. Without the window, a treatment
  arm enrolled in January is compared against a control arm still
  accumulating conversions in June, and the comparison flatters whichever arm
  has been running longer.

**Rate metrics and value metrics are not the same measurement.** The
two-proportion test this product reports — lift, minimum detectable effect,
significance — asks whether a larger *share* of accounts converted. It says
nothing about how much each conversion was worth. A metric like
`net_revenue_28d` is a value metric: the event underneath it is counted and
tested, and the value is reported beside the count and explicitly not tested,
because a mean-difference test on a heavy-tailed revenue distribution is a
different piece of statistics and claiming it here would be the failure this
product exists to prevent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

RATE = "rate"
VALUE = "value"

# The outcome types the runtime records. `reply_positive` comes from triage,
# the rest from a provider or CRM event (`runtime.engine.inbound`).
OUTCOME_TYPES = ("reply_positive", "meeting", "opp_created", "won")


class MetricError(ValueError):
    """A primary metric the runtime cannot measure."""


@dataclass(frozen=True)
class Metric:
    """One declared metric, resolved into something countable."""
    name: str
    events: tuple[str, ...]
    window_days: int
    kind: str = RATE
    # What the metric means in the words a program's owner would use, so a
    # console can say what is being counted rather than echoing the name.
    describes: str = ""

    @property
    def tests_value(self) -> bool:
        """Whether the *amount* is part of the verdict. It never is: a value
        metric's event is tested and its value is reported beside it."""
        return False


def _metric(name: str, events: tuple[str, ...], kind: str, describes: str) -> Metric:
    return Metric(name=name, events=events, window_days=_window_of(name), kind=kind,
                  describes=describes)


def _window_of(name: str) -> int:
    match = re.search(r"_(\d+)d$", name)
    if not match:
        raise MetricError(
            f"'{name}' names no window. A metric with no window compares a treatment arm "
            f"enrolled in January against a control arm still accumulating in June; "
            f"name it '{name}_90d' or similar")
    return int(match.group(1))


# The metrics a program may declare, and what each one counts. Adding a row is
# a decision about what the product measures, which is why they are listed
# rather than parsed out of the name: `revenue_uplift_14d` would otherwise be
# accepted and counted as nothing at all.
METRICS: dict[str, Metric] = {
    m.name: m for m in (
        _metric("opportunity_created_90d", ("opp_created",), RATE,
                "an opportunity opened in the CRM within ninety days of enrolment"),
        _metric("opportunity_created_60d", ("opp_created",), RATE,
                "an opportunity opened in the CRM within sixty days of enrolment"),
        _metric("meeting_booked_30d", ("meeting",), RATE,
                "a meeting booked within thirty days of enrolment"),
        _metric("signed_contract_60d", ("won",), RATE,
                "a deal marked won within sixty days of enrolment"),
        _metric("paid_conversion_30d", ("won",), RATE,
                "a deal marked won within thirty days of enrolment"),
        _metric("net_revenue_28d", ("won",), VALUE,
                "a deal marked won within twenty-eight days; the count is tested and "
                "the amount is reported beside it, never tested"),
        _metric("positive_reply_14d", ("reply_positive",), RATE,
                "a reply a person or triage read as positive, within fourteen days"),
        # Declared as a north star by two blueprints, so a program written for
        # either of them can name it without this module refusing.
        _metric("qualified_meeting_30d", ("meeting",), RATE,
                "a meeting booked within thirty days of enrolment"),
        _metric("qualified_opportunity_90d", ("opp_created",), RATE,
                "an opportunity opened within ninety days of enrolment"),
    )
}

# What the runtime counted before any of this existed, and what a program with
# no declared metric still falls back to. Kept as a named thing rather than a
# literal in three files: the measurement endpoint, the console and the frozen
# report all have to agree about what converts.
DEFAULT = Metric(name="any_conversion_90d",
                 events=("opp_created", "meeting", "reply_positive"),
                 window_days=90, kind=RATE,
                 describes="any recorded conversion within ninety days of enrolment")


def resolve(primary_metric: str | None) -> Metric:
    """The metric a program declared, or the default when it declared none.

    An unknown name raises rather than falling back. A program measured on
    something other than what it says it measures is the defect this module
    exists for, and a silent fallback is that defect wearing a default.
    """
    if not primary_metric:
        return DEFAULT
    metric = METRICS.get(primary_metric)
    if metric is None:
        raise MetricError(
            f"'{primary_metric}' is not a metric this runtime can measure. "
            f"Known: {', '.join(sorted(METRICS))}")
    return metric


def known() -> tuple[str, ...]:
    return tuple(sorted(METRICS))
