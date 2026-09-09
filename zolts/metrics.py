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
    """A metric the runtime cannot measure."""


# Outcomes only a contacted person can produce. They are recorded on the
# *touch* (`unsubscribed_at`, `complained_at`) rather than as outcomes, and the
# holdout is never touched — `planner` stops on `variant == "control"` as a
# product invariant. So a comparison of one against a holdout has a control arm
# that is structurally zero, is never resolvable, and would report *not
# resolvable* forever while looking like a measurement (D-76).
#
# They are real limits and they are already enforced, per mailbox and per
# domain, by the deliverability rules ADR-020 sets: 0.3% complaints pauses a
# domain, 2% unsubscribes triggers a review. A programme naming one as an
# experiment guardrail is asking the holdout a question the holdout cannot be
# asked, and the right answer is to say so rather than to measure nothing.
UNCOMPARABLE_TO_A_HOLDOUT: dict[str, str] = {
    "unsubscribe_rate": "unsubscribes are recorded on a touch and the holdout is never "
                        "touched, so the control arm is always zero",
    "spam_complaint_rate": "complaints are recorded on a touch and the holdout is never "
                           "touched, so the control arm is always zero",
    "complaint_rate": "complaints are recorded on a touch and the holdout is never "
                      "touched, so the control arm is always zero",
    "bounce_rate": "a bounce is a property of a send, and the holdout is never sent to",
}


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


def resolve_guardrail(name: str) -> Metric:
    """A guardrail metric, or the reason it can never be one.

    A guardrail asks whether the programme damaged something while it ran, and
    a holdout answers it the same way it answers the primary metric: both arms
    are watched, and a difference is a difference. **So a guardrail has to be
    something the holdout can also exhibit.** A churn, a repeat purchase, a
    margin — the control group can do all of those. It cannot unsubscribe from
    an email it was never sent.

    Refused here rather than measured to nothing, because a comparison whose
    control arm is structurally zero reports *not resolvable* forever and reads
    like a measurement that has not gathered enough data yet (D-76).
    """
    reason = UNCOMPARABLE_TO_A_HOLDOUT.get(name)
    if reason is not None:
        raise MetricError(
            f"'{name}' cannot be an experiment guardrail: {reason}. It is a real limit "
            f"and it is already enforced per mailbox and per domain by the "
            f"deliverability rules (ADR-020), which is where it belongs")
    metric = METRICS.get(name)
    if metric is None:
        raise MetricError(
            f"'{name}' is not a metric this runtime can measure. "
            f"Known: {', '.join(sorted(METRICS))}")
    if metric.kind != RATE:
        raise MetricError(
            f"'{name}' is a value metric, and a value metric cannot be a guardrail. "
            f"Its count is tested and its amount is reported beside the count and "
            f"never tested, so a breach in the amount — the thing a value guardrail "
            f"is for — has no verdict to report. Guard the rate instead")
    return metric


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
