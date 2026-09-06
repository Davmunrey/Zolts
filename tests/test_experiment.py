"""Holdout assignment must be stable, uniform and independent per program."""

import pytest

from zolts.experiment import (CONTROL, MIN_CONVERSIONS_PER_ARM, TREATMENT, assign,
                              is_resolvable, lift, minimum_detectable_effect)


def test_assignment_is_stable_across_calls():
    first = assign("account-42", "series-a-hiring-surge", 10, salt="2026q1")
    second = assign("account-42", "series-a-hiring-surge", 10, salt="2026q1")
    assert first == second


def test_assignment_is_stable_across_processes():
    # A literal digest, not a recomputation: catches any change to the hashing
    # scheme, which would silently reshuffle every running experiment.
    assert assign("account-42", "series-a-hiring-surge", 10, salt="2026q1").bucket == 2982


def test_holdout_is_uniform_at_scale():
    n = 100_000
    controls = sum(
        1 for i in range(n) if assign(f"acct-{i}", "prog", 10, salt="s").variant == CONTROL
    )
    observed = controls / n * 100
    assert abs(observed - 10) < 0.5, f"expected ~10% control, observed {observed:.2f}%"


def test_five_percent_holdout_is_resolvable():
    # 100 buckets would quantise a 5% holdout badly; 10_000 does not.
    n = 100_000
    controls = sum(1 for i in range(n) if assign(f"a{i}", "p", 5).variant == CONTROL)
    assert abs(controls / n * 100 - 5) < 0.4


def test_programs_are_independent():
    """The same account must not always land in control across programs."""
    n = 20_000
    both_control = same = 0
    for i in range(n):
        a = assign(f"acct-{i}", "program-a", 20, salt="s").variant
        b = assign(f"acct-{i}", "program-b", 20, salt="s").variant
        same += a == b
        both_control += a == CONTROL and b == CONTROL
    # Independent 20% holdouts co-occur ~4% of the time, not ~20%.
    assert abs(both_control / n - 0.04) < 0.01
    assert abs(same / n - 0.68) < 0.02


def test_zero_holdout_assigns_everyone_to_treatment():
    assert all(assign(f"a{i}", "p", 0).variant == TREATMENT for i in range(1000))


def test_holdout_above_fifty_percent_is_rejected():
    with pytest.raises(ValueError):
        assign("a", "p", 60)


def test_mde_shrinks_with_sample_size():
    small = minimum_detectable_effect(0.05, 500, 500)
    large = minimum_detectable_effect(0.05, 50_000, 50_000)
    assert small > large
    # A 500-per-arm test cannot resolve a 2-point lift on a 5% baseline.
    assert small > 0.02
    assert large < 0.005


def test_lift_is_undefined_on_a_zero_base():
    assert lift(0.05, 0.0)["relative"] == float("inf")


# -- what may be declared at all ----------------------------------------

def test_an_arm_with_too_few_conversions_resolves_nothing():
    """A control arm with no conversions does not establish a baseline.

    Found by rendering: the console reported EUR 1.75m incremental against a
    control arm of 26 subjects with zero observed conversions. The MDE looked
    precise because it had been handed a 1% floor rather than an estimate.
    """
    assert not is_resolvable(58, 0)
    assert not is_resolvable(58, 4)
    assert not is_resolvable(4, 58)
    assert is_resolvable(58, 5)


def test_the_threshold_is_the_normal_approximations_floor():
    assert MIN_CONVERSIONS_PER_ARM == 5
