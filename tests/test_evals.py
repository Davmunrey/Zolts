"""The eval gate. What may be sent without a human looking at it.

Every test here is about a way the gate could wrongly say yes. A gate that
wrongly says no costs a review; a gate that wrongly says yes sends something
nobody approved under a customer's brand.
"""

from __future__ import annotations

import pytest

from zolts.evals import (TIER_THRESHOLDS, Outcome, brand_check, evaluate, gate,
                         unit_check)

GOOD = ("Hi Dana. Northwind opened four RevOps roles last quarter, which usually means "
        "the reporting layer is about to be rebuilt. We helped Kestrel cut onboarding "
        "time by 40 percent through the same transition. Worth twenty minutes next week? "
        "Reply unsubscribe and I will stop.")


def _result(message=GOOD, **kwargs):
    kwargs.setdefault("factuality", 1.0)
    return evaluate(message, **kwargs)


# -- deterministic checks ------------------------------------------------

def test_an_unrendered_placeholder_fails_the_unit_check():
    """The most embarrassing possible send."""
    check = unit_check("Hi {{first_name}}, " + GOOD)
    assert check.outcome is Outcome.FAIL and "placeholder" in check.detail


@pytest.mark.parametrize("message,reason", [
    ("Too short.", "below"),
    (" ".join(["word"] * 400), "above"),
])
def test_length_bounds_are_enforced(message, reason):
    assert reason in unit_check(message).detail


def test_marketing_language_costs_score():
    clean = brand_check("We helped Kestrel cut onboarding time by 40 percent.")
    noisy = brand_check("Our revolutionary, world-class platform delivers seamless synergy.")
    assert clean.score == 1.0
    assert noisy.score < clean.score and noisy.outcome is Outcome.FAIL


# -- the veto ------------------------------------------------------------

def test_a_missing_opt_out_blocks_however_good_the_message():
    """Compliance is a veto, not a term in an average.

    Averaged, a well-written message that breaks the law clears the bar.
    """
    result = _result(GOOD.replace(" Reply unsubscribe and I will stop.", ""))
    assert result.score is not None and result.score > 0.9
    decision = gate(result, tier="t2", policy_allows=True, tenant_enabled=True)
    assert not decision.auto_send and "compliance" in decision.reason


def test_a_required_ai_disclosure_blocks_when_absent():
    result = _result(requires_ai_disclosure=True)
    assert not gate(result, tier="t2", policy_allows=True, tenant_enabled=True).auto_send


# -- the gate ------------------------------------------------------------

def test_a_clean_message_clears_tier_two():
    decision = gate(_result(), tier="t2", policy_allows=True, tenant_enabled=True)
    assert decision.auto_send and decision.score >= 0.85


def test_tier_one_never_auto_sends():
    """Not a high threshold. A refusal."""
    assert TIER_THRESHOLDS["t1"] is None
    decision = gate(_result(), tier="t1", policy_allows=True, tenant_enabled=True)
    assert not decision.auto_send and "never auto-sends" in decision.reason


def test_tier_three_is_stricter_than_tier_two():
    """Higher, because nobody reviews tier 3 at scale."""
    assert TIER_THRESHOLDS["t3"] > TIER_THRESHOLDS["t2"]


def test_an_unmeasured_check_blocks_rather_than_passing():
    """An absent check is not a pass.

    Treating 'no factuality verifier configured' as 1.0 turns every
    unconfigured tenant into an unattended sender.
    """
    result = _result(factuality=None)
    assert result.score is None
    assert "factuality" in result.unmeasured
    decision = gate(result, tier="t2", policy_allows=True, tenant_enabled=True)
    assert not decision.auto_send and "unmeasured" in decision.reason


def test_a_policy_denial_blocks_regardless_of_score():
    decision = gate(_result(), tier="t2", policy_allows=False, tenant_enabled=True)
    assert not decision.auto_send and "policy" in decision.reason


def test_a_tenant_that_has_not_opted_in_does_not_auto_send():
    decision = gate(_result(), tier="t2", policy_allows=True, tenant_enabled=False)
    assert not decision.auto_send


def test_low_factuality_blocks():
    decision = gate(_result(factuality=0.5), tier="t2", policy_allows=True, tenant_enabled=True)
    assert not decision.auto_send and "below" in decision.reason


def test_compliance_is_reported_before_score_because_they_need_different_fixes():
    """One is a lawyer's problem, the other is a prompt's."""
    bad = GOOD.replace(" Reply unsubscribe and I will stop.", "")
    decision = gate(_result(bad, factuality=0.2), tier="t2", policy_allows=True,
                    tenant_enabled=True)
    assert "compliance" in decision.reason
