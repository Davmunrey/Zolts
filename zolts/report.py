"""The incrementality report: what one program did, against its holdout, frozen.

`docs/10` says every program reports a real income statement and that the
lift is only ever reported beside the effect the sample can detect. `docs/17`
says the month-9 conversation with a CFO compares after with before. The
"before" is frozen (ADR-042); this is the "after", and it is frozen the same
way, at the close of a billing period, so that the number a partner reads in
month three is the number the row holds in month nine (D-46).

Pure logic. Everything derived — rates, lift, the minimum detectable effect,
the verdict, the pipeline — is derived here, once, and stored beside its
inputs, so a change to a formula never restates a signed document. The
digest is over the canonical fields, inputs and derived alike, so anybody
holding the report can recompute both the figures and the hash.

Three words are the whole verdict vocabulary: **significant**, **not
significant**, **not resolvable**. "Met" is not one of them. A criterion that
is not significant at the end of a term is reported as not significant
(`docs/26`, the letter), and a comparison whose control arm has fewer than
five observed conversions is not a result at all.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date

from zolts.experiment import (MIN_CONVERSIONS_PER_ARM, is_resolvable,
                              minimum_detectable_effect)
from zolts.metrics import DEFAULT as DEFAULT_METRIC

# What counts when a program declares no primary metric. A program that does
# declare one is measured on that metric's own events inside its own window
# (`zolts.metrics`, D-51); this is the fallback, defined there so the
# measurement endpoint, the console and this module cannot disagree.
CONVERSION_TYPES = DEFAULT_METRIC.events

SIGNIFICANT = "significant"
NOT_SIGNIFICANT = "not significant"
NOT_RESOLVABLE = "not resolvable"
VERDICTS = (SIGNIFICANT, NOT_SIGNIFICANT, NOT_RESOLVABLE)

# The floor the measurement endpoint and the console already apply to the
# control rate before computing an MDE: a control arm converting at zero would
# make the detectable effect zero, and then any lift at all "clears" it.
BASELINE_RATE_FLOOR = 0.01

# A month, for prorating a monthly run-rate across a period that is not one.
# The Gregorian mean rather than 30, so twelve consecutive periods sum to the
# declared year instead of eleven and a half of it.
DAYS_PER_MONTH = 365.2425 / 12

# The shape of `canonical()`. A frozen report is verified two ways: hash the
# JSON you hold, which never depended on this code, and rebuild it through
# `from_mapping` to recompute both the figures and the hash. The second is what
# `docs/10` sells, and it broke silently the first time the key set grew — a
# report frozen in one month, rebuilt the next, produced a digest its own letter
# did not carry (D-72). Every change to the key set is a new version, the old
# key sets stay here, and a rebuild recomputes the shape the document declares
# rather than the shape this code happens to be at.
SCHEMA_VERSION = 3

# Keys introduced after version 1. A body that declares no version *is* version
# 1: every report frozen before the form was versioned at all.
KEYS_ADDED_IN: dict[int, tuple[str, ...]] = {
    2: ("schema_version", "max_cost_per_meeting_micros", "run_rate_spend_micros",
        "acquisition_spend_micros", "cost_per_incremental_meeting_micros",
        "cost_per_meeting_withheld_because", "over_cost_per_meeting_ceiling"),
    3: ("period_spend_micros", "own_spend_micros", "own_spend_basis"),
}

# What the tenant's own spend for a period rests on. `declared` is a figure an
# operator recorded for that period; `run rate` is the onboarding baseline
# prorated by days, which nothing re-measures (decision 46). The report names
# which, because a reader cannot tell a measurement from an assumption by
# looking at the number.
DECLARED, RUN_RATE = "declared", "run rate"


class ReportError(ValueError):
    """A report that cannot be composed as given."""


@dataclass(frozen=True)
class Comparison:
    """Two arms, and what may be said about the difference between them.

    The same rules the measurement endpoint applies, in one place: no verdict
    below five conversions in either arm, an MDE from the floored control
    rate, and "significant" only when the lift clears it.
    """
    treatment_enrolled: int
    control_enrolled: int
    treatment_converted: int
    control_converted: int

    def __post_init__(self) -> None:
        for name in ("treatment_enrolled", "control_enrolled",
                     "treatment_converted", "control_converted"):
            if getattr(self, name) < 0:
                raise ReportError(f"{name} is negative")
        if self.treatment_converted > self.treatment_enrolled:
            raise ReportError("more treatment conversions than treatment enrollments")
        if self.control_converted > self.control_enrolled:
            raise ReportError("more control conversions than control enrollments")

    @property
    def treatment_rate(self) -> float | None:
        """None, not zero, with nobody enrolled: a rate of nothing is not 0%."""
        return (self.treatment_converted / self.treatment_enrolled
                if self.treatment_enrolled else None)

    @property
    def control_rate(self) -> float | None:
        return (self.control_converted / self.control_enrolled
                if self.control_enrolled else None)

    @property
    def resolvable(self) -> bool:
        return (self.treatment_enrolled > 0 and self.control_enrolled > 0
                and is_resolvable(self.treatment_converted, self.control_converted))

    @property
    def lift(self) -> float | None:
        """Absolute lift as a rate difference, whenever both arms have anyone."""
        if self.treatment_rate is None or self.control_rate is None:
            return None
        return self.treatment_rate - self.control_rate

    @property
    def minimum_detectable_effect(self) -> float | None:
        """Absolute, as a rate difference. None when nothing may be declared."""
        if not self.resolvable:
            return None
        return minimum_detectable_effect(
            baseline_rate=max(self.control_rate or 0.0, BASELINE_RATE_FLOOR),
            n_treatment=self.treatment_enrolled, n_control=self.control_enrolled)

    @property
    def verdict(self) -> str:
        if not self.resolvable:
            return NOT_RESOLVABLE
        lift = self.lift or 0.0
        return SIGNIFICANT if lift > (self.minimum_detectable_effect or 0.0) else NOT_SIGNIFICANT

    @property
    def why_not_resolvable(self) -> str | None:
        if self.resolvable:
            return None
        if not self.treatment_enrolled and not self.control_enrolled:
            return "nobody was enrolled"
        if not self.control_enrolled:
            return "no control arm yet"
        if not self.treatment_enrolled:
            return "no treatment arm yet"
        return (f"fewer than {MIN_CONVERSIONS_PER_ARM} conversions in an arm; "
                "the baseline is not established")

    @property
    def incremental_conversions(self) -> int | None:
        """How many conversions the holdout says would not have happened.
        Only when significant: an increment under the detectable effect is
        noise wearing an integer."""
        if self.verdict != SIGNIFICANT:
            return None
        return round((self.lift or 0.0) * self.treatment_enrolled)

    def canonical(self) -> dict[str, object]:
        return {
            "treatment_enrolled": self.treatment_enrolled,
            "control_enrolled": self.control_enrolled,
            "treatment_converted": self.treatment_converted,
            "control_converted": self.control_converted,
            "treatment_rate": _round(self.treatment_rate),
            "control_rate": _round(self.control_rate),
            "lift": _round(self.lift),
            "minimum_detectable_effect": _round(self.minimum_detectable_effect),
            "verdict": self.verdict,
            "incremental_conversions": self.incremental_conversions,
        }


def _round(value: float | None, places: int = 6) -> float | None:
    return None if value is None else round(value, places)


def ceiling_micros(spec: dict) -> int | None:
    """A program's declared acquisition-cost ceiling, in micros.

    Budgets are typed in euros, because that is what a person writing a
    program types; every money field the runtime holds is in micros. The
    conversion lives here, in the core, so the runtime that freezes a report
    and the builder that renders the demo cannot disagree about what a
    customer's number meant.

    A ceiling of zero is a ceiling, not an absence, so the test is against
    None rather than falsiness (decision 45).
    """
    declared = ((spec or {}).get("budget") or {}).get("max_cost_per_meeting")
    if declared is None:
        return None
    return round(float(declared) * 1_000_000)


@dataclass(frozen=True)
class BaselineQuote:
    """What the report quotes from the frozen baseline. The digest is the
    join: the row it came from can be checked against the letter."""
    digest: str
    window_start: date
    window_end: date
    monthly_spend_micros: int
    meetings: int
    opportunities: int
    cost_per_meeting_micros: int | None
    cost_per_opportunity_micros: int | None

    def canonical(self) -> dict[str, object]:
        return {
            "digest": self.digest,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "monthly_spend_micros": self.monthly_spend_micros,
            "meetings": self.meetings,
            "opportunities": self.opportunities,
            "cost_per_meeting_micros": self.cost_per_meeting_micros,
            "cost_per_opportunity_micros": self.cost_per_opportunity_micros,
        }


@dataclass(frozen=True)
class IncrementalityReport:
    """One program, from its first enrollment to the end of one billing period.

    Cumulative rather than sliced per month, because a three-month pilot cut
    into three months is three comparisons too small to resolve, and because
    a report per window invites choosing the window. The only comparison in
    it is treatment against a concurrent control (`docs/10`): nothing here
    compares one period with another.
    """
    program_key: str
    program_version: str
    spec_hash: str
    period_start: date
    period_end: date
    holdout_pct: float
    primary: Comparison
    opportunities: Comparison
    # What the primary comparison counted, and for how long after each account
    # entered. Part of the digest: two reports with the same arms and
    # different metrics are two different claims (D-51).
    primary_metric: str = DEFAULT_METRIC.name
    metric_window_days: int = DEFAULT_METRIC.window_days
    # Conversions by outcome type and arm, so the composition of the primary
    # number is visible: a lift made of positive replies is not a lift made
    # of opportunities, and the reader should not have to trust that.
    converted_by_type: dict[str, dict[str, int]] = field(default_factory=dict)
    # Conversions nobody read (decision 16). Counted, and disclosed.
    unread_conversions: int = 0
    touches_sent: int = 0
    decisions: dict[str, int] = field(default_factory=dict)
    # What the customer was billed, by kind, in credits. Never the company's
    # cost: a report a partner holds is not the place for our margin.
    credits_by_kind: dict[str, float] = field(default_factory=dict)
    # The tenant's own deals, from the CRM. None when the CRM holds no amount:
    # a pipeline figure from a number nobody synced is an assumption dressed
    # as a measurement.
    average_opportunity_micros: int | None = None
    opportunities_with_amount: int = 0
    # The acquisition-cost ceiling the programme declared, in micros. Carried
    # in the report rather than fetched from the spec, because a reader holding
    # the signed document must be able to see the figure and the ceiling it was
    # measured against without also holding the programme (decision 45).
    max_cost_per_meeting_micros: int | None = None
    # What the tenant declared they spent on go-to-market during this period,
    # in micros. None when nobody declared, and the run-rate is used instead.
    # Zero is a declaration and not an absence: a tenant who ran the period on
    # Zolts alone means the four zeros they wrote (decision 46).
    period_spend_micros: int | None = None
    baseline: BaselineQuote | None = None
    # The canonical shape this report was written in. Defaults to the current
    # one for a report being composed now, and is read back from the body for
    # one being rebuilt, so verifying an older document does not require an
    # older checkout (D-72).
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.period_end <= self.period_start:
            raise ReportError("the period ends before it starts")
        if not 0 <= self.holdout_pct <= 50:
            raise ReportError(f"holdout_pct must be within [0, 50], got {self.holdout_pct}")
        if self.unread_conversions < 0 or self.touches_sent < 0:
            raise ReportError("counts cannot be negative")
        if (self.unread_conversions
                > self.primary.treatment_converted + self.primary.control_converted):
            raise ReportError("more unread conversions than conversions")

    @property
    def enrolled(self) -> int:
        return self.primary.treatment_enrolled + self.primary.control_enrolled

    @property
    def conversions(self) -> int:
        return self.primary.treatment_converted + self.primary.control_converted

    @property
    def unread_share(self) -> float | None:
        return self.unread_conversions / self.conversions if self.conversions else None

    @property
    def verdict(self) -> str:
        return self.primary.verdict

    @property
    def credits_total(self) -> float:
        return round(sum(self.credits_by_kind.values()), 4)

    @property
    def incremental_pipeline_micros(self) -> int | None:
        """`docs/10`: absolute lift × treatment × average opportunity value —
        on opportunities, never on replies, and only when that comparison is
        itself significant and the CRM holds a value to multiply by."""
        increment = self.opportunities.incremental_conversions
        if increment is None or self.average_opportunity_micros is None:
            return None
        return increment * self.average_opportunity_micros

    @property
    def meetings(self) -> Comparison:
        """The meeting arm, assembled from counts the digest already covers.

        `converted_by_type` holds meetings per arm and `primary` holds the
        enrolment, so this costs no query and introduces no number a reader
        cannot re-derive from the document in front of them.
        """
        booked = self.converted_by_type.get("meeting", {})
        return Comparison(
            treatment_enrolled=self.primary.treatment_enrolled,
            control_enrolled=self.primary.control_enrolled,
            treatment_converted=int(booked.get("treatment", 0)),
            control_converted=int(booked.get("control", 0)))

    @property
    def period_days(self) -> int:
        return (self.period_end - self.period_start).days

    @property
    def run_rate_spend_micros(self) -> int | None:
        """What the customer's own go-to-market cost over this period, at the
        run-rate they declared at onboarding, prorated by days.

        An assumption, and the document says so: nothing re-measures a
        customer's salaries and tooling after the baseline is frozen. It is
        carried because the alternative is worse — a cost per meeting made of
        Zolts credits alone reads as €0.42 on the demo's own numbers, beside a
        baseline of €1,297 a meeting in the same document and a ceiling a
        shipped programme declares at €180: three figures that are not the same
        measurement (decision 46).

        None when no baseline was declared. A run-rate of zero would put the
        whole cost of a meeting on the platform fee.
        """
        if self.baseline is None:
            return None
        return round(self.baseline.monthly_spend_micros * self.period_days
                     / DAYS_PER_MONTH)

    @property
    def own_spend_micros(self) -> int | None:
        """The customer's own go-to-market spend for this period.

        A figure declared *for this period* when there is one, and the
        onboarding run-rate prorated by days when there is not. Never both and
        never a blend: a declaration replaces the assumption rather than
        adjusting it, and prorating a declaration would put the assumption
        back (decision 46).
        """
        if self.period_spend_micros is not None:
            return self.period_spend_micros
        return self.run_rate_spend_micros

    @property
    def own_spend_basis(self) -> str | None:
        """`declared` or `run rate`, and None when there is neither.

        Part of the digest, because the same figure means different things: one
        is a measurement of this period and the other is an assumption carried
        from the tenant's first day, and a reader cannot tell them apart by
        looking at the number.
        """
        if self.period_spend_micros is not None:
            return DECLARED
        return None if self.baseline is None else RUN_RATE

    @property
    def acquisition_spend_micros(self) -> int | None:
        """Everything acquiring cost this period: the customer's own spend plus
        what Zolts billed.

        A credit is a cent, which is ten thousand micros. Stated here rather
        than inferred, because a wrong conversion is a hundredfold error in a
        number a CFO reads.
        """
        own = self.own_spend_micros
        if own is None:
            return None
        return own + round(self.credits_total * 10_000)

    @property
    def cost_per_incremental_meeting_micros(self) -> int | None:
        """What a meeting this programme caused actually cost, all in.

        Per *incremental* meeting, not per meeting observed: the holdout books
        meetings this programme did not pay for, and dividing spend by all of
        them would flatter the figure by exactly the amount the holdout exists
        to measure. Same rule as the pipeline figure — only when the comparison
        clears its own detectable effect (`docs/10`).

        The same scope as the baseline quoted beside it, so the two are a
        before and an after rather than two different measurements sharing a
        name.
        """
        increment = self.meetings.incremental_conversions
        spend = self.acquisition_spend_micros
        if not increment or spend is None:  # zero increment is unreachable, see below
            return None
        return round(spend / increment)

    @property
    def cost_per_meeting_withheld_because(self) -> str | None:
        """Why there is no figure, in the reader's own document.

        Two reasons, and never a zero increment: a significant meeting
        comparison always caused at least one meeting, because the
        five-conversion floor keeps the detectable effect above 0.5 divided by
        the treatment arm, so a lift that clears it cannot round to nothing.
        A third sentence for that case would be a branch no input can reach,
        which is the second half of this repository's commonest defect: code
        that is written, correct and never on the path. Proved by a sweep in
        `test_cost_per_meeting.py` rather than asserted here.
        """
        if self.cost_per_incremental_meeting_micros is not None:
            return None
        if self.meetings.verdict != SIGNIFICANT:
            reason = self.meetings.why_not_resolvable
            return (f"the meeting comparison is {self.meetings.verdict}"
                    + (f" ({reason})" if reason else ""))
        return ("neither a declaration for this period nor a baseline to prorate, "
                "so the customer's own go-to-market spend is unknown")

    @property
    def over_cost_per_meeting_ceiling(self) -> bool | None:
        """Whether the period finished above the declared ceiling.

        None when there is no ceiling or no figure — never False, which would
        read as *within budget* on a programme that has not yet produced a
        number to judge. Decision 45: this is reported and never acted on, and
        it is a whole reporting period rather than a day, because a programme
        is over its cost per meeting every day until the first one lands.
        """
        ceiling, figure = self.max_cost_per_meeting_micros, \
            self.cost_per_incremental_meeting_micros
        if ceiling is None or figure is None:
            return None
        return figure > ceiling

    @property
    def pipeline_withheld_because(self) -> str | None:
        if self.incremental_pipeline_micros is not None:
            return None
        if self.opportunities.verdict != SIGNIFICANT:
            reason = self.opportunities.why_not_resolvable
            return (f"the opportunity comparison is {self.opportunities.verdict}"
                    + (f" ({reason})" if reason else ""))
        return "the CRM holds no opportunity with an amount"

    def canonical(self) -> dict[str, object]:
        """Every field the digest covers, inputs and derived alike — in the
        shape this report's version declares, never the shape the current code
        would emit. That is what makes a document signed against an older
        version verifiable against a newer checkout (D-72)."""
        fields: dict[str, object] = {
            "program_key": self.program_key,
            "program_version": self.program_version,
            "spec_hash": self.spec_hash,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "holdout_pct": self.holdout_pct,
            "primary_metric": self.primary_metric,
            "metric_window_days": self.metric_window_days,
            "primary": self.primary.canonical(),
            "opportunities": self.opportunities.canonical(),
            "converted_by_type": {t: dict(sorted(arms.items()))
                                  for t, arms in sorted(self.converted_by_type.items())},
            "unread_conversions": self.unread_conversions,
            "unread_share": _round(self.unread_share),
            "touches_sent": self.touches_sent,
            "decisions": dict(sorted(self.decisions.items())),
            "credits_by_kind": {k: round(v, 4) for k, v in sorted(self.credits_by_kind.items())},
            "credits_total": self.credits_total,
            "average_opportunity_micros": self.average_opportunity_micros,
            "opportunities_with_amount": self.opportunities_with_amount,
            "incremental_pipeline_micros": self.incremental_pipeline_micros,
            "pipeline_withheld_because": self.pipeline_withheld_because,
            "max_cost_per_meeting_micros": self.max_cost_per_meeting_micros,
            "period_spend_micros": self.period_spend_micros,
            "run_rate_spend_micros": self.run_rate_spend_micros,
            "own_spend_micros": self.own_spend_micros,
            "own_spend_basis": self.own_spend_basis,
            "acquisition_spend_micros": self.acquisition_spend_micros,
            "cost_per_incremental_meeting_micros": self.cost_per_incremental_meeting_micros,
            "cost_per_meeting_withheld_because": self.cost_per_meeting_withheld_because,
            "over_cost_per_meeting_ceiling": self.over_cost_per_meeting_ceiling,
            "verdict": self.verdict,
            "baseline": self.baseline.canonical() if self.baseline else None,
            "schema_version": self.schema_version,
        }
        for version, keys in KEYS_ADDED_IN.items():
            if version > self.schema_version:
                for key in keys:
                    fields.pop(key, None)
        return fields

    def digest(self) -> str:
        """sha256 over the canonical form. Quoted by whoever signs the report,
        so the document and the row can be checked against each other."""
        blob = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    def _cost_per_meeting_lines(self, lines: list[str]) -> None:
        """The acquisition-cost section, against the ceiling the programme
        declared. Reported, never acted on: decision 45."""
        figure = self.cost_per_incremental_meeting_micros
        ceiling = self.max_cost_per_meeting_micros
        lines.extend(["", "## Cost per meeting", ""])
        if figure is None:
            lines.append(f"Not reported: {self.cost_per_meeting_withheld_because}.")
        else:
            m = self.meetings
            lines.append(
                f"{_eur(self.acquisition_spend_micros)} over "
                f"{m.incremental_conversions} incremental meetings "
                f"({m.treatment_converted} against {m.control_converted} in the control "
                f"arm): **{_eur(figure)}** per meeting this programme caused.")
            zolts_micros = round(self.credits_total * 10_000)
            if self.own_spend_basis == DECLARED:
                # A measurement of this period, so nothing is disclaimed.
                rests_on = (
                    f"{_eur(self.own_spend_micros)} of your own go-to-market spend, "
                    f"declared for this period")
            else:
                rests_on = (
                    f"{_eur(self.own_spend_micros)} of your own go-to-market spend over "
                    f"{self.period_days} days, at the monthly run-rate you declared at "
                    f"onboarding — an assumption, because nothing re-measures it; if your "
                    f"team or tooling has changed since, so has this figure. Declaring "
                    f"the period's own spend replaces it (decision 46)")
            lines.append(
                f"All in, on the same basis as the baseline above: {rests_on}, plus "
                f"{_credits(self.credits_total)} credits billed by Zolts "
                f"({_eur(zolts_micros)}).")
        if ceiling is not None:
            verdict = ("above" if self.over_cost_per_meeting_ceiling else "within") \
                if figure is not None else "not measured against"
            lines.append(
                f"The programme declares a ceiling of {_eur(ceiling)}; this period is "
                f"{verdict} it. The ceiling is reported and never enforced — a programme "
                f"is over it every day until the first meeting lands (decision 45).")

    def render_markdown(self) -> str:
        """The document a person reads. Stored verbatim when frozen, so a
        later change to this template never restates a signed report."""
        p, o = self.primary, self.opportunities
        lines = [
            f"# Incrementality report — {self.program_key} v{self.program_version}",
            "",
            f"From {self.period_start.isoformat()} to {self.period_end.isoformat()}, "
            f"treatment against a concurrent {_pct(self.holdout_pct)} holdout. "
            f"Digest `{self.digest()}`.",
            "",
            "## Verdict",
            "",
            f"**{self.verdict.capitalize()}.** "
            + _verdict_sentence(p),
            "",
            "## Arms",
            "",
            "| Arm | Enrolled | Converted | Rate |",
            "|---|---|---|---|",
            f"| Treatment | {p.treatment_enrolled} | {p.treatment_converted} "
            f"| {_pct_rate(p.treatment_rate)} |",
            f"| Control | {p.control_enrolled} | {p.control_converted} "
            f"| {_pct_rate(p.control_rate)} |",
            "",
            _lift_sentence(p) + f" A conversion here is what `{self.primary_metric}` "
            f"counts, inside {self.metric_window_days} days of enrolment. "
            f"Conversions counted: {self.conversions}; of them "
            f"resting on a reply nobody read: {self.unread_conversions}"
            + (f" ({_pct(round(100 * self.unread_share, 1))})"
               if self.unread_share is not None else "")
            + ".",
        ]
        if self.converted_by_type:
            lines += ["", "| Outcome | Treatment | Control |", "|---|---|---|"]
            for kind, arms in sorted(self.converted_by_type.items()):
                lines.append(f"| {kind} | {arms.get('treatment', 0)} "
                             f"| {arms.get('control', 0)} |")
        lines += [
            "",
            "## Pipeline",
            "",
        ]
        if self.incremental_pipeline_micros is not None:
            lines.append(
                f"{o.incremental_conversions} incremental opportunities "
                f"({o.treatment_converted} against {o.control_converted} in the control "
                f"arm, lift {_pp(o.lift)} over a detectable {_pp(o.minimum_detectable_effect)}), "
                f"at the CRM's own average of {_eur(self.average_opportunity_micros)} over "
                f"{self.opportunities_with_amount} opportunities: "
                f"**{_eur(self.incremental_pipeline_micros)}** of incremental pipeline.")
        else:
            lines.append(f"Not reported: {self.pipeline_withheld_because}.")

        # Acquisition cost, against the ceiling the programme declared.
        # Reported, never acted on: decision 45. Absent from a version that
        # never carried the figures, so re-rendering an older document produces
        # the document that was signed rather than a longer one (D-72).
        if self.schema_version >= 2:
            self._cost_per_meeting_lines(lines)
        lines += [
            "",
            "## What it rests on",
            "",
            f"{_n(self.touches_sent, 'touch', 'touches')} sent; policy decisions "
            + (", ".join(f"{n} {k}" for k, n in sorted(self.decisions.items())) or "none")
            + ". Every external action carries a recorded decision with a reason; "
              "a control enrollment is never contacted.",
            "",
            "## What it cost",
            "",
        ]
        if self.credits_by_kind:
            lines += ["| Kind | Credits |", "|---|---|"]
            for kind, credits in sorted(self.credits_by_kind.items()):
                lines.append(f"| {kind} | {_credits(credits)} |")
            lines.append(f"| **Total** | **{_credits(self.credits_total)}** |")
        else:
            lines.append("No credits were consumed by this program in the period.")
        lines += ["", "## Before", ""]
        if self.baseline:
            b = self.baseline
            lines.append(
                f"Baseline `{b.digest}`: {b.window_start.isoformat()} to "
                f"{b.window_end.isoformat()}, monthly GTM spend {_eur(b.monthly_spend_micros)}, "
                f"{b.meetings} meetings and {b.opportunities} opportunities in the window — "
                f"cost per meeting {_eur(b.cost_per_meeting_micros)}, per opportunity "
                f"{_eur(b.cost_per_opportunity_micros)}. The comparison above is against the "
                f"concurrent control, not against this window; the window says what the "
                f"same money bought before.")
        else:
            lines.append("No baseline was frozen for this tenant before the program ran "
                         "(decision 35). There is nothing before to set this beside.")
        return "\n".join(lines) + "\n"


def _lift_sentence(p: Comparison) -> str:
    if p.lift is None:
        return "No lift is computed: both arms need somebody in them."
    if p.minimum_detectable_effect is None:
        return (f"Absolute lift {_pp(p.lift)}; no minimum detectable effect is computed "
                "for a comparison that is not resolvable.")
    return (f"Absolute lift {_pp(p.lift)}; minimum detectable effect "
            f"{_pp(p.minimum_detectable_effect)}.")


def _n(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _verdict_sentence(p: Comparison) -> str:
    if p.verdict == NOT_RESOLVABLE:
        return (f"No effect may be declared: {p.why_not_resolvable}. This is neither a "
                "failed criterion nor a passed one; it is a sample that cannot yet say.")
    if p.verdict == NOT_SIGNIFICANT:
        return ("The lift sits below the effect this sample can detect, so it is reported "
                "as not significant. A lift under the detectable effect is not a smaller "
                "result; it is no result.")
    return ("The lift clears the minimum detectable effect against a concurrent control, "
            f"and {p.incremental_conversions} conversions are attributed to the program.")


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:g}%"


def _pct_rate(rate: float | None) -> str:
    return "—" if rate is None else f"{100 * rate:.2f}%"


def _pp(diff: float | None) -> str:
    return "—" if diff is None else f"{100 * diff:+.2f} pp"


def _eur(micros: int | None) -> str:
    return "—" if micros is None else f"€{micros / 1_000_000:,.0f}"


def _credits(value: float) -> str:
    """Credits, for a document a person reads.

    `:g` switches to exponential above six significant digits, so a programme
    that billed 1,200,000 credits printed `1.2e+06` in a signed report (D-70).
    This keeps what `:g` was there for — no trailing zeros on a whole number —
    and adds the separator, because the figure is money at a hundred to the
    euro and nobody reads seven digits unbroken.
    """
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def from_mapping(data: dict[str, object]) -> IncrementalityReport:
    """A report rebuilt from its canonical form, so the digest can be
    recomputed by anybody holding the JSON and nothing else.

    Including a document older than this code: the rebuild takes the shape from
    the body's own `schema_version` and recomputes that shape, so a letter
    signed against version 1 still verifies after the form has grown (D-72).
    """
    try:
        primary = data["primary"]
        opps = data["opportunities"]
        baseline = data.get("baseline")
        return IncrementalityReport(
            program_key=str(data["program_key"]),
            program_version=str(data["program_version"]),
            spec_hash=str(data["spec_hash"]),
            period_start=date.fromisoformat(str(data["period_start"])),
            period_end=date.fromisoformat(str(data["period_end"])),
            holdout_pct=float(data["holdout_pct"]),
            primary_metric=str(data.get("primary_metric") or DEFAULT_METRIC.name),
            metric_window_days=int(data.get("metric_window_days")
                                   or DEFAULT_METRIC.window_days),
            primary=_comparison(primary),
            opportunities=_comparison(opps),
            converted_by_type={str(t): {str(a): int(n) for a, n in arms.items()}
                               for t, arms in (data.get("converted_by_type") or {}).items()},
            unread_conversions=int(data.get("unread_conversions") or 0),
            touches_sent=int(data.get("touches_sent") or 0),
            decisions={str(k): int(v) for k, v in (data.get("decisions") or {}).items()},
            credits_by_kind={str(k): float(v)
                             for k, v in (data.get("credits_by_kind") or {}).items()},
            average_opportunity_micros=(None if data.get("average_opportunity_micros") is None
                                        else int(data["average_opportunity_micros"])),
            opportunities_with_amount=int(data.get("opportunities_with_amount") or 0),
            # `or 0` would turn a declared ceiling of zero into no ceiling, and
            # a missing one into a ceiling of zero. Both change the digest of a
            # report somebody has already signed.
            max_cost_per_meeting_micros=(
                None if data.get("max_cost_per_meeting_micros") is None
                else int(data["max_cost_per_meeting_micros"])),
            # A body with no version is version 1, not the current one: every
            # report frozen before the form was versioned carries no such key,
            # and reading it as today's shape is the defect (D-72).
            # `or None` would turn a declared zero into no declaration, which
            # is the difference between a tenant who spent nothing outside
            # Zolts and one who never told us.
            period_spend_micros=(None if data.get("period_spend_micros") is None
                                 else int(data["period_spend_micros"])),
            schema_version=int(data.get("schema_version") or 1),
            baseline=None if not baseline else BaselineQuote(
                digest=str(baseline["digest"]),
                window_start=date.fromisoformat(str(baseline["window_start"])),
                window_end=date.fromisoformat(str(baseline["window_end"])),
                monthly_spend_micros=int(baseline["monthly_spend_micros"]),
                meetings=int(baseline["meetings"]),
                opportunities=int(baseline["opportunities"]),
                cost_per_meeting_micros=(None if baseline.get("cost_per_meeting_micros") is None
                                         else int(baseline["cost_per_meeting_micros"])),
                cost_per_opportunity_micros=(
                    None if baseline.get("cost_per_opportunity_micros") is None
                    else int(baseline["cost_per_opportunity_micros"]))),
        )
    except KeyError as exc:
        raise ReportError(f"missing field {exc.args[0]!r}") from exc
    except (TypeError, ValueError, AttributeError) as exc:
        raise ReportError(str(exc)) from exc


def _comparison(data: dict[str, object]) -> Comparison:
    return Comparison(
        treatment_enrolled=int(data["treatment_enrolled"]),
        control_enrolled=int(data["control_enrolled"]),
        treatment_converted=int(data["treatment_converted"]),
        control_converted=int(data["control_converted"]))
