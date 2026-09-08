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

# Outcomes that count as the primary conversion. A program declares its own
# primary metric; until outcome types are mapped per program this is the set
# the runtime records, and it is named once, here, so the measurement
# endpoint, the console and the report cannot disagree about what converts.
CONVERSION_TYPES = ("opp_created", "meeting", "reply_positive")

SIGNIFICANT = "significant"
NOT_SIGNIFICANT = "not significant"
NOT_RESOLVABLE = "not resolvable"
VERDICTS = (SIGNIFICANT, NOT_SIGNIFICANT, NOT_RESOLVABLE)

# The floor the measurement endpoint and the console already apply to the
# control rate before computing an MDE: a control arm converting at zero would
# make the detectable effect zero, and then any lift at all "clears" it.
BASELINE_RATE_FLOOR = 0.01


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
    baseline: BaselineQuote | None = None

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
    def pipeline_withheld_because(self) -> str | None:
        if self.incremental_pipeline_micros is not None:
            return None
        if self.opportunities.verdict != SIGNIFICANT:
            reason = self.opportunities.why_not_resolvable
            return (f"the opportunity comparison is {self.opportunities.verdict}"
                    + (f" ({reason})" if reason else ""))
        return "the CRM holds no opportunity with an amount"

    def canonical(self) -> dict[str, object]:
        """Every field the digest covers, inputs and derived alike."""
        return {
            "program_key": self.program_key,
            "program_version": self.program_version,
            "spec_hash": self.spec_hash,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "holdout_pct": self.holdout_pct,
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
            "verdict": self.verdict,
            "baseline": self.baseline.canonical() if self.baseline else None,
        }

    def digest(self) -> str:
        """sha256 over the canonical form. Quoted by whoever signs the report,
        so the document and the row can be checked against each other."""
        blob = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

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
            _lift_sentence(p) + " A conversion is a positive reply, a meeting or an "
            f"opportunity created. Conversions counted: {self.conversions}; of them "
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
                lines.append(f"| {kind} | {credits:g} |")
            lines.append(f"| **Total** | **{self.credits_total:g}** |")
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


def from_mapping(data: dict[str, object]) -> IncrementalityReport:
    """A report rebuilt from its canonical form, so the digest can be
    recomputed by anybody holding the JSON and nothing else."""
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
