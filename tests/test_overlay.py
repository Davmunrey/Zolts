"""Overlay resolution: inheritance adapts, but it may only tighten policy."""

import pytest

from zolts.overlay import Layer, PolicyLoosened, resolve

BLUEPRINT = Layer("blueprint:b2b-saas-sales-led", {
    "signals": ["funding.round", "hiring.role_opened"],
    "policy": {
        "max_touches_per_person_per_week": 3,
        "lists_check": ["global_suppression"],
        "channels_require_basis": {"email": "legitimate_interest"},
    },
})

INDUSTRY = Layer("pack:fintech", {
    "signals": ["funding.round", "hiring.role_opened", "regulatory.licence_granted"],
    "policy": {"lists_check": ["global_suppression", "regulator_watchlist"]},
})

TENANT = Layer("tenant:acme", {
    "policy": {"max_touches_per_person_per_week": 2},
    "custom_fields": {"policy_number": "string"},
})


def test_layers_merge_in_order():
    resolved = resolve([BLUEPRINT, INDUSTRY, TENANT])
    assert "regulatory.licence_granted" in resolved["signals"]
    assert resolved["custom_fields"] == {"policy_number": "string"}


def test_tenant_may_tighten_a_numeric_limit():
    resolved = resolve([BLUEPRINT, TENANT])
    assert resolved["policy"]["max_touches_per_person_per_week"] == 2


def test_tenant_may_not_loosen_a_numeric_limit():
    loose = Layer("tenant:loose", {"policy": {"max_touches_per_person_per_week": 10}})
    with pytest.raises(PolicyLoosened, match="max_touches_per_person_per_week"):
        resolve([BLUEPRINT, loose])


def test_suppression_lists_may_be_added_but_never_dropped():
    assert "regulator_watchlist" in resolve([BLUEPRINT, INDUSTRY])["policy"]["lists_check"]
    dropping = Layer("tenant:dropping", {"policy": {"lists_check": ["regulator_watchlist"]}})
    with pytest.raises(PolicyLoosened, match="global_suppression"):
        resolve([BLUEPRINT, INDUSTRY, dropping])


def test_required_basis_may_be_strengthened():
    stricter = Layer("tenant:strict", {
        "policy": {"channels_require_basis": {"email": "consent"}},
    })
    resolved = resolve([BLUEPRINT, stricter])
    assert resolved["policy"]["channels_require_basis"]["email"] == "consent"


def test_required_basis_may_not_be_weakened():
    consent_blueprint = Layer("blueprint:b2c", {
        "policy": {"channels_require_basis": {"email": "consent"}},
    })
    weakening = Layer("tenant:weak", {
        "policy": {"channels_require_basis": {"email": "legitimate_interest"}},
    })
    with pytest.raises(PolicyLoosened, match="email"):
        resolve([consent_blueprint, weakening])


def test_a_new_channel_may_be_added_with_its_own_basis():
    adding = Layer("tenant:adding", {
        "policy": {"channels_require_basis": {"whatsapp": "consent"}},
    })
    resolved = resolve([BLUEPRINT, adding])
    assert resolved["policy"]["channels_require_basis"] == {
        "email": "legitimate_interest", "whatsapp": "consent",
    }


def test_resolution_is_deterministic():
    assert resolve([BLUEPRINT, INDUSTRY, TENANT]) == resolve([BLUEPRINT, INDUSTRY, TENANT])


def test_non_policy_keys_merge_without_restriction():
    """Adaptability lives here: tenants extend freely outside policy."""
    resolved = resolve([BLUEPRINT, Layer("t", {"signals": ["own.signal"]})])
    assert resolved["signals"] == ["own.signal"]
