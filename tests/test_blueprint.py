"""Blueprint resolution: the adaptability claim, made falsifiable.

docs/05 claims a company profile resolves to an archetype through a
deterministic decision table rather than a consulting interview. The claim is
only worth something if each archetype's own canonical customer actually lands
on it, and if a profile that contradicts an archetype's legal basis can never
land on it at all.
"""

import pytest
import yaml

from zolts.blueprint import (
    BLUEPRINT_DIR,
    DIMENSIONS,
    CompanyProfile,
    ProfileError,
    load_blueprints,
    resolve,
    score,
)

BLUEPRINTS = load_blueprints()
BY_KEY = {b.key: b for b in BLUEPRINTS}


def profile(**overrides) -> CompanyProfile:
    """A B2B SaaS sales-led profile, the entry archetype, as the base."""
    base = dict(
        motion="slg", acv_band="10-50k", cycle_length="3-9m", customer_type="b2b",
        icp_breadth="mid", geography="eu", data_maturity="warehouse", crm="hubspot",
        sales_team="5-25", product_type="software", compliance_tier="standard",
        channels=("email", "linkedin"), sector="software",
    )
    base.update(overrides)
    return CompanyProfile(**base)


# The canonical customer of each archetype, written from its own description.
CANONICAL = {
    "b2b-saas-sales-led": profile(),
    "b2b-saas-plg": profile(motion="plg", acv_band="1-10k", cycle_length="1-3m",
                            icp_breadth="mass", sales_team="1-5",
                            channels=("email", "product")),
    "b2b-enterprise": profile(motion="abm", acv_band=">250k", cycle_length=">9m",
                              icp_breadth="niche", crm="salesforce", sales_team=">25"),
    "services-agency": profile(product_type="services", sector="services", motion="mixed",
                               icp_breadth="mass", crm="pipedrive", sales_team="1-5",
                               channels=("email", "linkedin", "voice")),
    "fintech-regulated": profile(compliance_tier="regulated", sector="financial_services", cycle_length=">9m",
                                 acv_band="50-250k", crm="salesforce", sales_team=">25"),
    "healthtech-lifesci": profile(compliance_tier="regulated", sector="life_sciences", motion="abm",
                                  acv_band=">250k", cycle_length=">9m",
                                  icp_breadth="niche", crm="salesforce", sales_team=">25"),
    "ecommerce-dtc": profile(customer_type="b2c", sector="retail", motion="self_serve", acv_band="<1k",
                             cycle_length="<7d", icp_breadth="mass",
                             product_type="physical", crm="none", sales_team="0",
                             channels=("email", "whatsapp", "ads")),
    "marketplace-2sided": profile(customer_type="b2b2c", sector="marketplace", motion="self_serve",
                                  acv_band="1-10k", cycle_length="<7d",
                                  icp_breadth="mass", product_type="marketplace",
                                  crm="attio", sales_team="1-5",
                                  channels=("email", "ads")),
    "industrial-b2b": profile(product_type="physical", sector="manufacturing", motion="channel",
                              acv_band=">250k", cycle_length=">9m", icp_breadth="niche",
                              crm="salesforce", data_maturity="none",
                              channels=("email", "voice")),
    "local-services-multisite": profile(product_type="services", sector="healthcare", acv_band="1-10k",
                                        cycle_length="<7d", icp_breadth="mass",
                                        crm="pipedrive", sales_team="1-5",
                                        data_maturity="none",
                                        channels=("voice", "whatsapp")),
    "public-sector": profile(customer_type="public", sector="public", compliance_tier="public_sector",
                             acv_band=">250k", cycle_length=">9m", icp_breadth="niche",
                             motion="channel", crm="salesforce", sales_team=">25",
                             channels=("email", "voice")),
}


def test_every_archetype_has_a_blueprint_file():
    assert len(BLUEPRINTS) == 11
    assert set(BY_KEY) == set(CANONICAL)


@pytest.mark.parametrize("key", sorted(CANONICAL), ids=lambda k: k)
def test_canonical_profile_resolves_to_its_own_archetype(key):
    """The core adaptability claim. If this fails, the decision table is wrong."""
    result = resolve(CANONICAL[key], BLUEPRINTS)
    assert result.blueprint.key == key, (
        f"{key} canonical profile resolved to {result.blueprint.key}\n{result.explain()}"
    )


@pytest.mark.parametrize("key", sorted(CANONICAL), ids=lambda k: k)
def test_resolution_is_not_a_coin_flip(key):
    """A margin of zero means the table cannot actually tell two archetypes
    apart, which would make the choice arbitrary rather than deterministic."""
    result = resolve(CANONICAL[key], BLUEPRINTS)
    assert result.margin > 0, f"{key} ties with {result.runner_up.key}"


def test_a_b2c_profile_never_lands_on_a_b2b_archetype():
    """customer_type is a hard dimension: getting it wrong means the wrong
    legal basis, which is a compliance failure rather than a poor fit."""
    result = resolve(CANONICAL["ecommerce-dtc"], BLUEPRINTS)
    assert result.blueprint.key in {"ecommerce-dtc", "marketplace-2sided"}
    b2b_only = score(CANONICAL["ecommerce-dtc"], BY_KEY["b2b-saas-sales-led"])
    assert not b2b_only.eligible
    assert "customer_type" in b2b_only.disqualified_by


def test_a_regulated_profile_never_lands_on_a_standard_archetype():
    regulated = profile(compliance_tier="regulated")
    candidate = score(regulated, BY_KEY["b2b-saas-sales-led"])
    assert not candidate.eligible
    assert "compliance_tier" in candidate.disqualified_by


def test_resolution_is_deterministic():
    a = resolve(CANONICAL["b2b-saas-sales-led"], BLUEPRINTS)
    b = resolve(CANONICAL["b2b-saas-sales-led"], BLUEPRINTS)
    assert a.blueprint.key == b.blueprint.key and a.score == b.score


def test_an_unmatched_profile_still_resolves_and_is_flagged():
    """A company whose hard dimensions match nothing is not turned away at
    onboarding; it lands somewhere and a human reviews the policy pack."""
    odd = profile(customer_type="b2b", compliance_tier="unheard_of")
    result = resolve(odd, BLUEPRINTS)
    assert result.fallback is True
    assert result.blueprint is not None


def test_explanation_names_every_dimension():
    """An unexplained archetype choice is one the customer will argue with."""
    text = resolve(CANONICAL["b2b-saas-sales-led"], BLUEPRINTS).explain()
    for dimension in DIMENSIONS:
        assert dimension in text


def test_channel_overlap_is_enough_to_match():
    """A company reachable by email and LinkedIn fits a blueprint that needs
    either; requiring the full set would exclude almost every real customer."""
    narrow = profile(channels=("email",))
    assert score(narrow, BY_KEY["b2b-saas-sales-led"]).eligible


def test_a_profile_missing_a_dimension_is_rejected():
    with pytest.raises(ProfileError):
        CompanyProfile(
            motion="slg", acv_band="", cycle_length="3-9m", customer_type="b2b",
            icp_breadth="mid", geography="eu", data_maturity="warehouse", crm="hubspot",
            sales_team="5-25", product_type="software", compliance_tier="standard",
            channels=("email",), sector="software",
        )


@pytest.mark.parametrize("key", sorted(CANONICAL), ids=lambda k: k)
def test_every_blueprint_declares_a_real_holdout(key):
    """The product invariant reaches the archetype defaults too."""
    assert BY_KEY[key].spec["default_holdout_pct"] >= 5


@pytest.mark.parametrize("key", sorted(CANONICAL), ids=lambda k: k)
def test_every_blueprint_declares_signals_programs_and_a_north_star(key):
    spec = BY_KEY[key].spec
    assert spec["signals"] and spec["programs"] and spec["north_star"]
    assert spec["policy"]["channels_require_basis"]


@pytest.mark.parametrize("key", sorted(CANONICAL), ids=lambda k: k)
def test_every_blueprint_checks_the_global_suppression_list(key):
    """Three separate opt-out defects in this repository were all silent.
    No archetype ships without the global suppression list."""
    assert "global_suppression" in BY_KEY[key].spec["policy"]["lists_check"]


def test_b2c_archetypes_require_consent_for_every_declared_channel():
    """B2C cannot rest on legitimate interest. This is the rule that would be
    quietly lost if an archetype were copied from a B2B one."""
    for key in ("ecommerce-dtc", "marketplace-2sided"):
        basis = BY_KEY[key].spec["policy"]["channels_require_basis"]
        assert set(basis.values()) == {"consent"}, f"{key}: {basis}"


def test_weights_are_declared_for_every_dimension():
    for blueprint in BLUEPRINTS:
        missing = [d for d in DIMENSIONS if d not in blueprint.weights]
        assert not missing, f"{blueprint.key} missing weights for {missing}"


def test_blueprint_files_declare_every_matchable_dimension():
    for path in sorted(BLUEPRINT_DIR.glob("*.yaml")):
        matches = yaml.safe_load(path.read_text())["spec"]["matches"]
        missing = [d for d in DIMENSIONS if d not in matches]
        assert not missing, f"{path.name} missing matches for {missing}"
