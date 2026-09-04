"""The waterfall optimiser must beat the static baseline, and by how much."""

from zolts.waterfall import (
    Provider,
    brute_force_optimum,
    expected_coverage,
    expected_cost,
    optimise,
    static_waterfall,
)

# A realistic three-provider mix per docs/07 D3: cheap coverage, premium, verifier.
# Hit rates differ by cohort, which is precisely what a static order ignores.
PROVIDERS = [
    Provider(
        key="broad_cheap",
        unit_cost=0.008,
        accuracy=0.91,
        hit_rates={"es_smb": 0.72, "de_mid": 0.41, "us_ent": 0.55},
    ),
    Provider(
        key="premium",
        unit_cost=0.045,
        accuracy=0.97,
        hit_rates={"es_smb": 0.61, "de_mid": 0.83, "us_ent": 0.88},
    ),
    Provider(
        key="regional",
        unit_cost=0.021,
        accuracy=0.94,
        hit_rates={"es_smb": 0.68, "de_mid": 0.35, "us_ent": 0.22},
    ),
]


def test_greedy_matches_brute_force():
    """The cost/hit-rate ordering must actually be optimal, not merely cited."""
    for cohort in ("es_smb", "de_mid", "us_ent"):
        greedy = optimise(PROVIDERS, cohort)
        _, best_cost = brute_force_optimum(PROVIDERS, cohort)
        assert abs(greedy.expected_cost - best_cost) < 1e-12, cohort


def test_optimiser_beats_static_waterfall_per_cohort():
    savings = {}
    for cohort in ("es_smb", "de_mid", "us_ent"):
        optimised = optimise(PROVIDERS, cohort)
        static = static_waterfall(PROVIDERS, cohort)
        saving = 1 - optimised.cost_per_verified_contact / static.cost_per_verified_contact
        savings[cohort] = saving
        assert saving >= 0, f"{cohort}: optimiser must never be worse"
    # Coverage is identical, so any saving is pure cost reduction.
    assert savings["es_smb"] > 0.5, savings


def test_coverage_is_unchanged_by_ordering():
    """Reordering changes cost, never reach. Any drop would be a real regression."""
    for cohort in ("es_smb", "de_mid", "us_ent"):
        assert abs(optimise(PROVIDERS, cohort).expected_coverage
                   - static_waterfall(PROVIDERS, cohort).expected_coverage) < 1e-12


def test_accuracy_sla_excludes_weak_providers():
    plan = optimise(PROVIDERS, "es_smb", accuracy_sla=0.95)
    assert plan.order == ("premium",)
    assert plan.weighted_accuracy >= 0.95


def test_coverage_target_truncates_the_waterfall():
    """Savings layer 4: stop calling providers once the target is met."""
    full = optimise(PROVIDERS, "es_smb")
    truncated = optimise(PROVIDERS, "es_smb", coverage_target=0.80)
    assert len(truncated.order) < len(full.order)
    assert truncated.expected_cost < full.expected_cost
    assert truncated.expected_coverage >= 0.80


def test_cost_per_verified_contact_amortises_over_hits():
    """Cost per call understates the real number; the P&L needs cost per hit."""
    single = [Provider(key="p", unit_cost=0.10, accuracy=0.99, default_hit_rate=0.5)]
    plan = optimise(single, "any")
    assert abs(plan.expected_cost - 0.10) < 1e-12
    assert abs(plan.cost_per_verified_contact - 0.20) < 1e-12


def test_no_eligible_provider_yields_an_empty_plan():
    plan = optimise(PROVIDERS, "cohort_with_no_data")
    assert plan.order == ()
    assert plan.expected_cost == 0.0


def test_expected_cost_matches_the_closed_form():
    order = [PROVIDERS[0], PROVIDERS[1]]
    manual = 0.008 + 0.045 * (1 - 0.72)
    assert abs(expected_cost(order, "es_smb") - manual) < 1e-12
    assert abs(expected_coverage(order, "es_smb") - (1 - 0.28 * 0.39)) < 1e-12


# --- Regression guard over the benchmark model in scripts/benchmark_waterfall.py.
# These lock in the measured behaviour so an algorithm change cannot silently
# erode the saving the plan sells.

def _benchmark_savings():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from benchmark_waterfall import COHORTS, PROVIDERS  # noqa: PLC0415

    savings = []
    for cohort in COHORTS:
        opt = optimise(PROVIDERS, cohort)
        static = static_waterfall(PROVIDERS, cohort)
        savings.append(1 - opt.cost_per_verified_contact / static.cost_per_verified_contact)
    return dict(zip(COHORTS, savings))


def test_median_saving_clears_the_phase_two_exit_criterion():
    import statistics

    savings = _benchmark_savings()
    assert statistics.median(savings.values()) >= 0.30


def test_every_cohort_clears_the_floor():
    """No cohort may fall below 20%, even where cheap coverage is weak."""
    savings = _benchmark_savings()
    worst = min(savings.items(), key=lambda kv: kv[1])
    assert worst[1] >= 0.20, f"worst cohort {worst[0]} at {worst[1]:.1%}"


def test_saving_is_weakest_where_cheap_coverage_is_weakest():
    """The structural finding: the optimiser's edge is bounded by how good the
    cheap provider is in that market. DACH and the Nordics are where the
    '-30% cost' pitch is weakest, and they are ACV-attractive markets."""
    savings = _benchmark_savings()
    assert savings["de_ent"] < savings["es_smb"]
    assert savings["de_ent"] < savings["latam_smb"]
