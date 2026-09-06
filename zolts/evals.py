"""The eval gate: whether a generated message may be sent without a human.

From `docs/08`: a message goes out unattended only if `eval_score >= the tier
threshold` **and** `policy = allow` **and** the tenant enabled auto-send for
that tier. Defaults: tier 1 never, tier 2 0.85, tier 3 0.90 — higher, because
nobody reviews tier 3 at scale.

Two things here are deliberate and easy to get wrong.

**Compliance is a veto, not a score.** Averaging a missing opt-out link into a
weighted mean lets a well-written message that breaks the law clear the bar.
Compliance failures block regardless of everything else.

**An absent check is not a pass.** A score that has not been measured is
recorded as unmeasured and blocks auto-send. The alternative — treating
"no brand classifier configured" as 1.0 — turns every unconfigured tenant into
an unattended sender.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

# Tier thresholds from docs/08. Tier 1 is not a number: it is a refusal.
TIER_THRESHOLDS: dict[str, float | None] = {"t1": None, "t2": 0.85, "t3": 0.90}

# What the aggregate is made of. Compliance is absent on purpose — it vetoes
# rather than contributing, and giving it a weight would let it be outvoted.
WEIGHTS = {"unit": 0.25, "factuality": 0.50, "brand": 0.25}

_PLACEHOLDER = re.compile(r"\{\{.*?\}\}|\[\[.*?\]\]|\{[A-Za-z_]+\}|XXX|TODO|Lorem ipsum",
                          re.IGNORECASE)
_SUPERLATIVE = re.compile(
    r"\b(revolutionary|game.?chang\w+|world.?class|cutting.?edge|best.in.class|"
    r"unparalleled|unmatched|seamless\w*|synergy|leverage our|10x|guaranteed)\b",
    re.IGNORECASE)


class Outcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNMEASURED = "unmeasured"


@dataclass(frozen=True)
class Check:
    name: str
    outcome: Outcome
    score: float | None = None
    detail: str | None = None

    @property
    def blocking(self) -> bool:
        return self.outcome is not Outcome.PASS


@dataclass
class EvalResult:
    checks: list[Check] = field(default_factory=list)
    compliance: list[Check] = field(default_factory=list)

    def by_name(self, name: str) -> Check | None:
        return next((c for c in self.checks if c.name == name), None)

    @property
    def unmeasured(self) -> list[str]:
        return [c.name for c in self.checks + self.compliance
                if c.outcome is Outcome.UNMEASURED]

    @property
    def compliance_failures(self) -> list[str]:
        return [c.detail or c.name for c in self.compliance if c.blocking]

    @property
    def score(self) -> float | None:
        """The weighted aggregate, or None when a component was not measured.

        None rather than a partial average: an average over the checks that
        happened to run is a number whose meaning changes with the
        configuration, and it would be compared against a fixed threshold.
        """
        total = 0.0
        for name, weight in WEIGHTS.items():
            check = self.by_name(name)
            if check is None or check.score is None:
                return None
            total += weight * check.score
        return round(total, 4)


# -- deterministic checks -------------------------------------------------

def unit_check(message: str, *, min_words: int = 20, max_words: int = 220,
               required: tuple[str, ...] = ()) -> Check:
    """Format, length, required fields, no leftover placeholders."""
    problems: list[str] = []
    leftover = _PLACEHOLDER.findall(message or "")
    if leftover:
        problems.append(f"unrendered placeholder: {leftover[0]}")
    words = len((message or "").split())
    if words < min_words:
        problems.append(f"{words} words, below the {min_words} floor")
    if words > max_words:
        problems.append(f"{words} words, above the {max_words} ceiling")
    for field_name in required:
        if field_name not in (message or ""):
            problems.append(f"missing required element '{field_name}'")
    if problems:
        return Check("unit", Outcome.FAIL, 0.0, "; ".join(problems))
    return Check("unit", Outcome.PASS, 1.0)


def brand_check(message: str, *, prohibited: tuple[str, ...] = ()) -> Check:
    """Prohibited claims and the superlatives every buyer has learned to skip.

    A per-tenant trained classifier is the eventual mechanism; this is the
    deterministic floor under it, and it is scored rather than vetoed because
    tone is a matter of degree.
    """
    hits = [m.group(0) for m in _SUPERLATIVE.finditer(message or "")]
    hits += [word for word in prohibited if word.lower() in (message or "").lower()]
    if not hits:
        return Check("brand", Outcome.PASS, 1.0)
    # Each hit costs a fifth. Five pieces of marketing language in one message
    # is not a tone problem, it is a different message.
    score = max(0.0, 1.0 - 0.2 * len(hits))
    outcome = Outcome.PASS if score >= 0.8 else Outcome.FAIL
    return Check("brand", outcome, score, f"off-voice: {', '.join(sorted(set(hits))[:4])}")


def compliance_checks(message: str, *, requires_ai_disclosure: bool = False,
                      requires_opt_out: bool = True,
                      opt_out_markers: tuple[str, ...] = ("unsubscribe", "opt out", "opt-out",
                                                          "baja", "darse de baja"),
                      disclosure_markers: tuple[str, ...] = ("ai-assisted", "ai assisted",
                                                             "generated with ai",
                                                             "asistido por ia")) -> list[Check]:
    """Blocking checks. Each is a legal obligation, not a quality signal."""
    body = (message or "").lower()
    checks: list[Check] = []
    if requires_opt_out:
        present = any(marker in body for marker in opt_out_markers)
        checks.append(Check("compliance.opt_out",
                            Outcome.PASS if present else Outcome.FAIL,
                            detail=None if present else "no opt-out instruction in the message"))
    if requires_ai_disclosure:
        present = any(marker in body for marker in disclosure_markers)
        checks.append(Check("compliance.ai_disclosure",
                            Outcome.PASS if present else Outcome.FAIL,
                            detail=None if present else "jurisdiction requires an AI disclosure"))
    return checks


def evaluate(message: str, *, factuality: float | None,
             requires_ai_disclosure: bool = False, requires_opt_out: bool = True,
             prohibited: tuple[str, ...] = (), required: tuple[str, ...] = (),
             min_words: int = 20, max_words: int = 220) -> EvalResult:
    result = EvalResult()
    result.checks.append(unit_check(message, min_words=min_words, max_words=max_words,
                                    required=required))
    result.checks.append(
        Check("factuality", Outcome.PASS if factuality is not None and factuality >= 0.99
              else Outcome.FAIL if factuality is not None else Outcome.UNMEASURED,
              factuality,
              None if factuality is None else f"{factuality:.0%} of claims carry provenance"))
    result.checks.append(brand_check(message, prohibited=prohibited))
    result.compliance = compliance_checks(message,
                                          requires_ai_disclosure=requires_ai_disclosure,
                                          requires_opt_out=requires_opt_out)
    return result


# -- the gate -------------------------------------------------------------

@dataclass(frozen=True)
class Gate:
    auto_send: bool
    reason: str
    score: float | None
    threshold: float | None


def gate(result: EvalResult, *, tier: str, policy_allows: bool,
         tenant_enabled: bool, threshold: float | None = None) -> Gate:
    """Decide whether this message may be sent without a human.

    Order matters, and it is worst-first: a compliance failure is reported as a
    compliance failure even when the score would also have blocked it, because
    the two need different fixes from different people.
    """
    configured = threshold if threshold is not None else TIER_THRESHOLDS.get(tier)
    score = result.score

    if result.compliance_failures:
        return Gate(False, f"compliance: {result.compliance_failures[0]}", score, configured)
    if not policy_allows:
        return Gate(False, "policy denied the action", score, configured)
    if not tenant_enabled:
        return Gate(False, "tenant has not enabled auto-send for this tier", score, configured)
    if configured is None:
        return Gate(False, f"tier '{tier}' never auto-sends", score, configured)
    if result.unmeasured:
        return Gate(False, f"unmeasured: {', '.join(result.unmeasured)}", score, configured)
    if score is None:
        return Gate(False, "no aggregate score could be computed", score, configured)
    if score < configured:
        return Gate(False, f"score {score:.2f} below the {configured:.2f} threshold",
                    score, configured)
    return Gate(True, f"score {score:.2f} clears the {configured:.2f} threshold",
                score, configured)
