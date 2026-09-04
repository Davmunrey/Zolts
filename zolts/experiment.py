"""Deterministic holdout assignment.

Claim under test (docs/10): assignment is stable across re-runs, uniform
across the population, and independent between programs sharing a salt
namespace. Anything else silently corrupts every lift measurement the
product sells.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

CONTROL = "control"
TREATMENT = "treatment"

_BUCKETS = 10_000  # sub-percent resolution; 100 buckets is too coarse for 5% holdouts


@dataclass(frozen=True)
class Assignment:
    variant: str
    bucket: int

    @property
    def is_control(self) -> bool:
        return self.variant == CONTROL


def bucket_of(entity_id: str, program_key: str, salt: str = "") -> int:
    """Map an entity to a stable bucket in [0, _BUCKETS).

    SHA-256 rather than Python's hash(): the latter is salted per process,
    so a restart would silently reshuffle every experiment.
    """
    digest = hashlib.sha256(f"{entity_id}|{program_key}|{salt}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % _BUCKETS


def assign(entity_id: str, program_key: str, holdout_pct: float, salt: str = "") -> Assignment:
    """Assign an entity to control or treatment.

    holdout_pct is a percentage (10 means 10%). Values outside [0, 50] are
    rejected: a holdout above half the population is never a real design,
    it is a misconfiguration that would halve reach without warning.
    """
    if not 0 <= holdout_pct <= 50:
        raise ValueError(f"holdout_pct must be within [0, 50], got {holdout_pct}")
    bucket = bucket_of(entity_id, program_key, salt)
    cutoff = holdout_pct / 100.0 * _BUCKETS
    variant = CONTROL if bucket < cutoff else TREATMENT
    return Assignment(variant=variant, bucket=bucket)


def minimum_detectable_effect(
    baseline_rate: float,
    n_treatment: int,
    n_control: int,
    alpha: float = 0.05,
    power: float = 0.8,
) -> float:
    """Absolute MDE for a two-proportion test.

    Returned as an absolute rate difference. The product refuses to publish a
    program whose MDE exceeds the lift the team expects: reporting a
    non-significant result as a success is the sector's most common failure.
    """
    if n_treatment <= 0 or n_control <= 0:
        return float("inf")
    z_alpha = 1.959963985  # two-sided 0.05
    z_beta = 0.8416212336  # power 0.80
    if abs(alpha - 0.05) > 1e-9 or abs(power - 0.8) > 1e-9:
        raise NotImplementedError("only alpha=0.05 and power=0.80 are tabulated")
    p = baseline_rate
    variance_term = p * (1 - p) * (1 / n_treatment + 1 / n_control)
    return (z_alpha + z_beta) * (variance_term ** 0.5)


def lift(treatment_rate: float, control_rate: float) -> dict[str, float]:
    """Absolute and relative lift. Relative lift is undefined at a zero base."""
    absolute = treatment_rate - control_rate
    relative = absolute / control_rate if control_rate > 0 else float("inf")
    return {"absolute": absolute, "relative": relative}
