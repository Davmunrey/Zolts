"""Blueprint resolution: company profile to archetype.

Claim under test (docs/05): adaptability to any company type comes from a
deterministic decision table over 12 profile dimensions, not from an open
consulting interview and not from a per-customer fork.

The resolver is deliberately boring. It scores every blueprint against the
profile, returns the winner with a per-dimension explanation, and breaks ties
by a stable rule. An LLM may later refine copy and prioritisation on top of
this; it never decides the archetype, because an archetype decides the policy
pack and policy must be auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

BLUEPRINT_DIR = Path(__file__).resolve().parent.parent / "blueprints"

# The profile dimensions. docs/05 originally specified twelve; `sector` is the
# thirteenth, added because twelve could not tell a regulated fintech from a
# regulated life-sciences company — their canonical profiles scored identically
# and one of them resolved to the generic enterprise archetype. A profile is
# only as good as its worst answer, so every dimension is required.
DIMENSIONS = (
    "motion", "acv_band", "cycle_length", "customer_type", "icp_breadth",
    "geography", "data_maturity", "crm", "sales_team", "product_type",
    "compliance_tier", "channels", "sector",
)

# Dimensions whose mismatch is disqualifying rather than merely costly.
# Getting customer_type or compliance_tier wrong means the wrong legal basis
# and the wrong policy pack, which is a compliance failure, not a bad fit.
HARD_DIMENSIONS = frozenset({"customer_type", "compliance_tier"})


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class CompanyProfile:
    motion: str
    acv_band: str
    cycle_length: str
    customer_type: str
    icp_breadth: str
    geography: str
    data_maturity: str
    crm: str
    sales_team: str
    product_type: str
    compliance_tier: str
    channels: tuple[str, ...]
    sector: str

    def __post_init__(self) -> None:
        missing = [d for d in DIMENSIONS if not getattr(self, d)]
        if missing:
            raise ProfileError(f"profile is missing required dimensions: {missing}")

    def as_dict(self) -> dict[str, Any]:
        return {d: getattr(self, d) for d in DIMENSIONS}


@dataclass(frozen=True)
class Blueprint:
    key: str
    name: str
    matches: dict[str, Any]
    weights: dict[str, float]
    spec: dict[str, Any]

    @property
    def programs(self) -> list[str]:
        return list(self.spec.get("programs", []))

    @property
    def signals(self) -> list[str]:
        return list(self.spec.get("signals", []))

    @property
    def policy(self) -> dict[str, Any]:
        return dict(self.spec.get("policy", {}))


@dataclass(frozen=True)
class DimensionMatch:
    dimension: str
    profile_value: Any
    accepted: tuple[str, ...]
    matched: bool
    weight: float
    hard: bool

    @property
    def points(self) -> float:
        return self.weight if self.matched else 0.0


@dataclass(frozen=True)
class Candidate:
    blueprint: Blueprint
    score: float
    disqualified_by: tuple[str, ...]
    matches: tuple[DimensionMatch, ...]

    @property
    def eligible(self) -> bool:
        return not self.disqualified_by


@dataclass(frozen=True)
class Resolution:
    blueprint: Blueprint
    score: float
    runner_up: Blueprint | None
    margin: float
    candidates: tuple[Candidate, ...]
    fallback: bool

    def explain(self) -> str:
        """Human-readable rationale. Shown at onboarding and stored with the tenant."""
        lines = [f"Resolved to {self.blueprint.key} (score {self.score:.2f})"]
        if self.fallback:
            lines.append("  no blueprint matched on the hard dimensions; fell back")
        chosen = next(c for c in self.candidates if c.blueprint.key == self.blueprint.key)
        for match in chosen.matches:
            mark = "+" if match.matched else " "
            lines.append(
                f"  {mark} {match.dimension}: {match.profile_value!r} "
                f"{'in' if match.matched else 'not in'} {list(match.accepted)} "
                f"(weight {match.weight})"
            )
        if self.runner_up:
            lines.append(f"  runner-up {self.runner_up.key}, margin {self.margin:.2f}")
        return "\n".join(lines)


def load_blueprints(directory: Path | None = None) -> list[Blueprint]:
    """Load every blueprint on disk, sorted by key for deterministic ordering."""
    source = directory or BLUEPRINT_DIR
    if not source.is_dir():
        # An absent directory and an empty one are not the same fact. Globbing
        # a path that does not exist returns nothing and reads as "this product
        # has no blueprints", which is how a container shipped without them
        # reported success and seeded an empty tenant.
        raise FileNotFoundError(
            f"blueprint directory {source} does not exist; the package or image "
            "was built without it")
    out: list[Blueprint] = []
    for path in sorted(source.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text())
        if raw.get("kind") != "Blueprint":
            raise ValueError(f"{path}: expected kind Blueprint, got {raw.get('kind')!r}")
        metadata, spec = raw["metadata"], raw["spec"]
        out.append(Blueprint(
            key=metadata["key"],
            name=metadata["name"],
            matches=spec["matches"],
            weights=spec.get("weights", {}),
            spec=spec,
        ))
    return out


def _accepted(blueprint: Blueprint, dimension: str) -> tuple[str, ...]:
    value = blueprint.matches.get(dimension, [])
    if isinstance(value, str):
        value = [value]
    return tuple(value)


def _matches(profile_value: Any, accepted: tuple[str, ...]) -> bool:
    """A wildcard accepts anything. A channel list matches on any overlap:
    a company that can use email and LinkedIn fits a blueprint needing either."""
    if "*" in accepted:
        return True
    if isinstance(profile_value, (list, tuple)):
        return any(v in accepted for v in profile_value)
    return profile_value in accepted


def score(profile: CompanyProfile, blueprint: Blueprint) -> Candidate:
    matches: list[DimensionMatch] = []
    disqualified: list[str] = []
    total = 0.0

    for dimension in DIMENSIONS:
        accepted = _accepted(blueprint, dimension)
        weight = float(blueprint.weights.get(dimension, 1.0))
        hard = dimension in HARD_DIMENSIONS
        value = getattr(profile, dimension)
        ok = _matches(value, accepted) if accepted else True

        matches.append(DimensionMatch(
            dimension=dimension, profile_value=value, accepted=accepted,
            matched=ok, weight=weight, hard=hard,
        ))
        if ok:
            total += weight
        elif hard:
            disqualified.append(dimension)

    return Candidate(
        blueprint=blueprint, score=total,
        disqualified_by=tuple(disqualified), matches=tuple(matches),
    )


def resolve(profile: CompanyProfile, blueprints: list[Blueprint] | None = None) -> Resolution:
    """Pick the archetype for a profile.

    Ties break on blueprint key, alphabetically. An arbitrary but stable rule
    beats a subtle one: the same profile must always land on the same
    blueprint, because the blueprint decides the policy pack.
    """
    catalogue = blueprints if blueprints is not None else load_blueprints()
    if not catalogue:
        raise ValueError("no blueprints available to resolve against")

    candidates = sorted(
        (score(profile, b) for b in catalogue),
        key=lambda c: (-c.score, c.blueprint.key),
    )
    eligible = [c for c in candidates if c.eligible]

    # Every profile resolves. A company whose hard dimensions match nothing is
    # not turned away at onboarding; it lands on the closest eligible-by-score
    # archetype and is flagged so a human reviews the policy pack.
    fallback = not eligible
    pool = eligible or candidates
    winner = pool[0]
    runner_up = pool[1] if len(pool) > 1 else None

    return Resolution(
        blueprint=winner.blueprint,
        score=winner.score,
        runner_up=runner_up.blueprint if runner_up else None,
        margin=winner.score - runner_up.score if runner_up else winner.score,
        candidates=tuple(candidates),
        fallback=fallback,
    )
