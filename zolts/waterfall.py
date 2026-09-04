"""Waterfall router: expected-cost optimiser over data providers.

Claim under test (docs/07): dynamic per-cohort ordering beats the static
waterfall teams configure by hand by 30-50% on cost per verified contact.
That number is asserted in the plan; `tests/test_waterfall.py` measures it.

Model: providers are queried in order until one returns a hit. For sequential
search with independent success probabilities, expected cost is

    E[cost] = sum_i c_i * prod_{j<i} (1 - h_j)

and the ordering that minimises it sorts by cost/hit-rate ascending. This is
the classic sequential-search result; `test_greedy_matches_brute_force`
verifies it against exhaustive permutation rather than trusting the citation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import permutations


@dataclass(frozen=True)
class Provider:
    key: str
    unit_cost: float          # EUR per call (billed on hit or on call, see billed_on_miss)
    accuracy: float           # measured share of returned values that are correct
    hit_rates: dict[str, float] = field(default_factory=dict)  # cohort -> P(hit)
    default_hit_rate: float = 0.0
    billed_on_miss: bool = False

    def hit_rate(self, cohort: str) -> float:
        return self.hit_rates.get(cohort, self.default_hit_rate)


@dataclass(frozen=True)
class Plan:
    order: tuple[str, ...]
    expected_cost: float
    expected_coverage: float
    weighted_accuracy: float

    @property
    def cost_per_verified_contact(self) -> float:
        """Cost amortised over contacts actually resolved, not over calls made.

        This is the number that belongs in a P&L: paying 0.10 EUR to resolve
        50% of a cohort costs 0.20 EUR per resolved contact, not 0.10.
        """
        if self.expected_coverage <= 0:
            return float("inf")
        return self.expected_cost / self.expected_coverage


def expected_cost(order: list[Provider], cohort: str) -> float:
    """Expected spend for one lookup under a given provider order."""
    total = 0.0
    reach_probability = 1.0
    for provider in order:
        total += provider.unit_cost * reach_probability
        reach_probability *= 1.0 - provider.hit_rate(cohort)
    return total


def expected_coverage(order: list[Provider], cohort: str) -> float:
    miss = 1.0
    for provider in order:
        miss *= 1.0 - provider.hit_rate(cohort)
    return 1.0 - miss


def weighted_accuracy(order: list[Provider], cohort: str) -> float:
    """Accuracy of the value actually delivered, weighted by who resolves it."""
    total, reach = 0.0, 1.0
    for provider in order:
        hit = provider.hit_rate(cohort)
        total += provider.accuracy * hit * reach
        reach *= 1.0 - hit
    resolved = 1.0 - reach
    return total / resolved if resolved > 0 else 0.0


def _sort_key(provider: Provider, cohort: str) -> float:
    """Cost per unit of hit probability. A provider that never hits is last."""
    hit = provider.hit_rate(cohort)
    return provider.unit_cost / hit if hit > 0 else float("inf")


def optimise(
    providers: list[Provider],
    cohort: str,
    accuracy_sla: float = 0.0,
    coverage_target: float = 0.0,
    max_cost: float | None = None,
) -> Plan:
    """Order providers to minimise expected cost subject to quality constraints.

    A provider whose measured accuracy is below the SLA is excluded outright:
    a hit from it would deliver a value the program promised not to send.
    Early stop is implicit in the sequential model — later providers are only
    paid for when every earlier one missed.
    """
    eligible = [p for p in providers if p.accuracy >= accuracy_sla and p.hit_rate(cohort) > 0]
    if not eligible:
        return Plan(order=(), expected_cost=0.0, expected_coverage=0.0, weighted_accuracy=0.0)

    ordered = sorted(eligible, key=lambda p: _sort_key(p, cohort))

    # Truncate once the coverage target is met: paying for a fourth provider
    # after the SLA is satisfied is pure waste (savings layer 4 in docs/07).
    if coverage_target > 0:
        kept: list[Provider] = []
        for provider in ordered:
            kept.append(provider)
            if expected_coverage(kept, cohort) >= coverage_target:
                break
        ordered = kept

    if max_cost is not None:
        affordable: list[Provider] = []
        for provider in ordered:
            candidate = affordable + [provider]
            if expected_cost(candidate, cohort) > max_cost:
                break
            affordable = candidate
        ordered = affordable

    return Plan(
        order=tuple(p.key for p in ordered),
        expected_cost=expected_cost(ordered, cohort),
        expected_coverage=expected_coverage(ordered, cohort),
        weighted_accuracy=weighted_accuracy(ordered, cohort),
    )


def brute_force_optimum(providers: list[Provider], cohort: str) -> tuple[tuple[str, ...], float]:
    """Exhaustive minimum over all orderings. Used only to verify the greedy rule."""
    best_order: tuple[str, ...] = ()
    best_cost = float("inf")
    for candidate in permutations(providers):
        cost = expected_cost(list(candidate), cohort)
        if cost < best_cost - 1e-12:
            best_cost = cost
            best_order = tuple(p.key for p in candidate)
    return best_order, best_cost


def static_waterfall(providers: list[Provider], cohort: str) -> Plan:
    """The hand-configured baseline: a fixed order, usually premium-first.

    This is what the optimiser is measured against. Teams set one global order
    and never revisit it per cohort, which is exactly where the money leaks.
    """
    ordered = sorted(providers, key=lambda p: -p.unit_cost)
    return Plan(
        order=tuple(p.key for p in ordered),
        expected_cost=expected_cost(ordered, cohort),
        expected_coverage=expected_coverage(ordered, cohort),
        weighted_accuracy=weighted_accuracy(ordered, cohort),
    )
