"""Signal decay and PIT-R scoring."""

import pytest

from zolts.scoring import Signal, intent_score, pit_r, timing_from_signals


def test_signal_halves_after_one_half_life():
    signal = Signal("funding.round", base_strength=0.8, half_life_h=720, age_h=720)
    assert abs(signal.decayed_strength() - 0.4) < 1e-9


def test_fresh_signal_keeps_full_strength():
    signal = Signal("web.pricing_page_visit", base_strength=0.9, half_life_h=48, age_h=0)
    assert abs(signal.decayed_strength() - 0.9) < 1e-9


def test_source_confidence_scales_strength():
    signal = Signal("intent.topic_surge", base_strength=0.6, half_life_h=720,
                    age_h=0, source_confidence=0.5)
    assert abs(signal.decayed_strength() - 0.3) < 1e-9


def test_intent_combines_probabilistically_not_additively():
    """Three 0.5 signals must not sum to 1.5; the union is 0.875."""
    signals = [Signal(f"s{i}", 0.5, 720, 0) for i in range(3)]
    assert abs(intent_score(signals) - 0.875) < 1e-9


def test_intent_is_bounded_below_one():
    """A long tail of weak signals can never saturate the score."""
    weak = [Signal(f"s{i}", 0.2, 720, 0) for i in range(50)]
    assert intent_score(weak) < 1.0


def test_one_strong_fresh_signal_beats_many_stale_ones():
    """The behaviour a summed score gets wrong, per docs/06."""
    fresh = [Signal("pricing_visit", 0.9, 48, 1)]
    stale = [Signal(f"s{i}", 0.6, 720, 2160) for i in range(6)]  # 3 half-lives old
    assert intent_score(fresh) > intent_score(stale)


def test_expired_signal_contributes_almost_nothing():
    expired = [Signal("funding.round", 0.8, 720, 7200)]  # 10 half-lives
    assert intent_score(expired) < 0.001


def test_pit_r_contributions_sum_to_the_score():
    score = pit_r(fit=0.8, intent=0.6, timing=0.9, reachability=1.0)
    assert abs(sum(score.contributions.values()) - score.value) < 1e-9


def test_pit_r_is_explainable_per_factor():
    score = pit_r(fit=1.0, intent=0.0, timing=0.0, reachability=0.0)
    assert abs(score.contributions["fit"] - 35.0) < 1e-9
    assert score.contributions["intent"] == 0.0
    assert "fit=35.0" in score.explain()


def test_perfect_account_scores_one_hundred():
    assert abs(pit_r(1.0, 1.0, 1.0, 1.0).value - 100.0) < 1e-9


def test_weights_must_sum_to_one():
    with pytest.raises(ValueError):
        pit_r(0.5, 0.5, 0.5, 0.5, weights={"fit": 0.5, "intent": 0.5,
                                           "timing": 0.5, "reachability": 0.5})


def test_out_of_range_factor_is_rejected():
    with pytest.raises(ValueError):
        pit_r(fit=1.4, intent=0.5, timing=0.5, reachability=0.5)


def test_timing_tracks_the_freshest_signal():
    signals = [Signal("old", 0.8, 720, 1440), Signal("new", 0.5, 48, 0)]
    assert abs(timing_from_signals(signals) - 1.0) < 1e-9


def test_timing_of_no_signals_is_zero():
    assert timing_from_signals([]) == 0.0
