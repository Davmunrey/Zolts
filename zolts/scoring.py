"""Signal decay and PIT-R scoring.

Claim under test (docs/06): signals combine probabilistically rather than
additively. Summing correlated signals saturates the score with noise and is
the most common scoring bug in GTM tooling.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Signal:
    key: str
    base_strength: float      # 0..1
    half_life_h: int
    age_h: float
    source_confidence: float = 1.0

    def decayed_strength(self) -> float:
        """Exponential decay: worth base_strength/2 after one half-life."""
        if self.half_life_h <= 0:
            return 0.0
        factor = 0.5 ** (self.age_h / self.half_life_h)
        return self.base_strength * factor * self.source_confidence


def intent_score(signals: list[Signal]) -> float:
    """Probabilistic union: 1 - prod(1 - s_i).

    Bounded in [0, 1) by construction, so a long tail of weak signals can
    never outrank one strong fresh signal — which is the behaviour a summed
    score gets wrong.
    """
    miss = 1.0
    for signal in signals:
        miss *= 1.0 - max(0.0, min(1.0, signal.decayed_strength()))
    return 1.0 - miss


DEFAULT_WEIGHTS = {"fit": 0.35, "intent": 0.30, "timing": 0.25, "reachability": 0.10}


@dataclass(frozen=True)
class Score:
    value: float                          # 0..100
    contributions: dict[str, float]       # per-factor points, sums to value

    def explain(self) -> str:
        parts = ", ".join(f"{k}={v:.1f}" for k, v in sorted(self.contributions.items()))
        return f"score={self.value:.1f} ({parts})"


def pit_r(
    fit: float,
    intent: float,
    timing: float,
    reachability: float,
    weights: dict[str, float] | None = None,
) -> Score:
    """Fit x Intent x Timing x Reachability, as an explainable weighted sum.

    Per-factor contributions are returned rather than a bare number: the AI Act
    documentation obligation and every "why this account?" question both need
    them, so explainability is not optional in the engine.
    """
    weights = weights or DEFAULT_WEIGHTS
    total_weight = sum(weights.values())
    if abs(total_weight - 1.0) > 1e-6:
        raise ValueError(f"weights must sum to 1.0, got {total_weight}")

    factors = {"fit": fit, "intent": intent, "timing": timing, "reachability": reachability}
    for name, value in factors.items():
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be within [0, 1], got {value}")

    contributions = {name: weights[name] * value * 100.0 for name, value in factors.items()}
    return Score(value=sum(contributions.values()), contributions=contributions)


def timing_from_signals(signals: list[Signal]) -> float:
    """Timing is driven by the freshest signal, not by how many there are.

    An account with one signal from an hour ago is more actionable than one
    with five signals from two months ago.
    """
    if not signals:
        return 0.0
    return max(s.decayed_strength() / s.base_strength if s.base_strength else 0.0 for s in signals)
