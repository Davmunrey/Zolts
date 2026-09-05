"""The console's data is derived, not written.

The surface used to carry hand-written programs and a second copy of the
minimum-detectable-effect formula in JavaScript. Two implementations of one
statistic drift, and the one on screen is the one a customer reads — so these
tests hold the derivation in place and prove the duplicate is gone.
"""

import re
from pathlib import Path

import pytest

from zolts.experiment import lift, minimum_detectable_effect

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from build_fixture import OBSERVED, build  # noqa: E402

CONSOLE = Path(__file__).resolve().parent.parent / "design" / "console.html"
FIXTURE = build()
BY_KEY = {p["key"]: p for p in FIXTURE["programs"]}


def test_every_shipped_program_appears():
    assert len(FIXTURE["programs"]) == 4
    assert set(BY_KEY) == set(OBSERVED)


@pytest.mark.parametrize("key", sorted(OBSERVED), ids=lambda k: k)
def test_mde_matches_the_reference_core(key):
    """The figure on screen must come from the function the suite covers."""
    p, seen = BY_KEY[key], OBSERVED[key]
    expected = minimum_detectable_effect(seen["ctrl"], p["nTreat"], p["nControl"])
    assert p["mde"] == round(expected * 100, 2)


@pytest.mark.parametrize("key", sorted(OBSERVED), ids=lambda k: k)
def test_lift_matches_the_reference_core(key):
    p, seen = BY_KEY[key], OBSERVED[key]
    assert p["absLift"] == round(lift(seen["treat"], seen["ctrl"])["absolute"] * 100, 2)


@pytest.mark.parametrize("key", sorted(OBSERVED), ids=lambda k: k)
def test_significance_is_lift_against_mde_not_a_flag(key):
    p = BY_KEY[key]
    assert p["significant"] is (p["absLift"] > p["mde"])


@pytest.mark.parametrize("key", sorted(OBSERVED), ids=lambda k: k)
def test_pipeline_is_reported_only_when_significant(key):
    """The product's central claim, enforced where the number is produced."""
    p = BY_KEY[key]
    if p["significant"]:
        assert p["pipeline"] and p["pipeline"] > 0
    else:
        assert p["pipeline"] is None


@pytest.mark.parametrize("key", sorted(OBSERVED), ids=lambda k: k)
def test_needed_holdout_actually_resolves_the_lift(key):
    """The surface tells the operator which holdout would make the lift
    reportable. That claim has to hold at the sample size it is offered for."""
    p, seen = BY_KEY[key], OBSERVED[key]
    if p["significant"]:
        return
    assert p["neededHoldout"] is not None
    n = seen["enrolled"]
    nc = round(n * p["neededHoldout"] / 100)
    assert lift(seen["treat"], seen["ctrl"])["absolute"] > \
        minimum_detectable_effect(seen["ctrl"], n - nc, nc)


@pytest.mark.parametrize("key", sorted(OBSERVED), ids=lambda k: k)
def test_mde_curve_is_monotonic_and_covers_the_slider_range(key):
    """A larger holdout resolves a smaller effect, up to the point where
    shrinking the treatment arm starts to cost more than it buys."""
    curve = BY_KEY[key]["mdeCurve"]
    assert set(curve) == {str(i) for i in range(5, 26)}
    assert curve["5"] > curve["15"]


def test_decisions_are_evaluated_per_jurisdiction():
    """Switching pack must change outcomes, or the switcher is decoration."""
    decisions = FIXTURE["decisions"]
    assert set(decisions) == set(FIXTURE["jurisdictions"])
    denials = {j: sum(r["decision"] == "deny" for r in rows) for j, rows in decisions.items()}
    assert denials["DE"] > denials["ES"], denials


def test_every_decision_carries_a_rule_key():
    for rows in FIXTURE["decisions"].values():
        for row in rows:
            assert row["rule"] and row["rationale"]


def test_console_carries_no_second_copy_of_the_formula():
    """The regression this wiring exists to prevent. The z-constants living in
    JavaScript would mean two implementations of one statistic."""
    source = CONSOLE.read_text()
    for constant in ("1.959963985", "0.8416212336", "Z_ALPHA", "Z_BETA"):
        assert constant not in source, f"{constant} is back in the console"


def test_console_carries_no_second_policy_engine():
    source = CONSOLE.read_text()
    for marker in ("function decide(", "STRENGTH =", "basis_required'"):
        assert marker not in source, f"{marker} is back in the console"


def test_console_reads_the_fixture_placeholder():
    assert "/*__FIXTURE__*/" in CONSOLE.read_text()


def test_program_names_come_from_the_yaml_not_the_console():
    source = CONSOLE.read_text()
    for p in FIXTURE["programs"]:
        assert p["name"] not in source, f"'{p['name']}' is hard-coded in the console"


def test_the_build_is_deterministic():
    """This output is committed and CI verifies it matches a fresh build, so a
    single moving field turns the staleness guard permanently red. A wall-clock
    timestamp did exactly that once; this is the guard against its return."""
    import json

    assert json.dumps(build(), sort_keys=True) == json.dumps(build(), sort_keys=True)


def test_the_fixture_carries_no_wall_clock_field():
    import json

    blob = json.dumps(build())
    assert "generatedAt" not in blob
    assert not re.search(r"20\d\d-\d\d-\d\dT\d\d:", blob), "a timestamp leaked into the fixture"
