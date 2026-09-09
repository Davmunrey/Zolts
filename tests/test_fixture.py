"""The console's data is derived, not written.

The surface used to carry hand-written programs and a second copy of the
minimum-detectable-effect formula in JavaScript. Two implementations of one
statistic drift, and the one on screen is the one a customer reads — so these
tests hold the derivation in place and prove the duplicate is gone.
"""

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zolts.experiment import is_resolvable, lift, minimum_detectable_effect

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
    if p["mde"] is None:
        # Withheld because the arm establishes no baseline. Covered by
        # test_an_unresolvable_arm_reports_no_effect_at_all.
        return
    expected = minimum_detectable_effect(seen["ctrl"], p["nTreat"], p["nControl"])
    assert p["mde"] == round(expected * 100, 2)


@pytest.mark.parametrize("key", sorted(OBSERVED), ids=lambda k: k)
def test_an_unresolvable_arm_reports_no_effect_at_all(key):
    """An arm with too few conversions establishes no baseline.

    Found by rendering the console against live data: it reported EUR 1.75m
    incremental against a control arm of 26 subjects with zero observed
    conversions. The MDE read as precise because it had been handed a floor
    rather than an estimate. When the baseline is not established, the MDE, the
    needed holdout and the pipeline are all withheld and the surface says why.
    """
    p = BY_KEY[key]
    conversions = (round(p["nTreat"] * p["treat"] / 100),
                   round(p["nControl"] * p["ctrl"] / 100))
    if is_resolvable(*conversions):
        assert p["unresolvedReason"] is None
        assert p["mde"] is not None
        return
    assert p["mde"] is None
    assert p["neededHoldout"] is None
    assert p["pipeline"] is None
    assert p["significant"] is False
    assert p["unresolvedReason"], "the surface must say why, not show a bare dash"


@pytest.mark.parametrize("key", sorted(OBSERVED), ids=lambda k: k)
def test_lift_matches_the_reference_core(key):
    p, seen = BY_KEY[key], OBSERVED[key]
    assert p["absLift"] == round(lift(seen["treat"], seen["ctrl"])["absolute"] * 100, 2)


@pytest.mark.parametrize("key", sorted(OBSERVED), ids=lambda k: k)
def test_significance_is_lift_against_mde_not_a_flag(key):
    p = BY_KEY[key]
    if p["mde"] is None:
        assert p["significant"] is False
        return
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
    if p["significant"] or p["mde"] is None:
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
    """No timestamp that *moves*, rather than no timestamp.

    This forbade every ISO timestamp, which was right while the fixture had
    none to keep. A policy decision and an audit entry are meaningless without
    when they happened, so the fixture now carries constants — and a rule that
    cannot tell a constant from a clock reading rejects them both.

    The rule is now the property: every timestamp in the fixture must be far
    enough from now that it cannot have been read off the clock. That still
    catches the one case the byte-identical test below cannot — a wall clock
    truncated to the minute, where two builds in the same minute agree — and it
    lets a constant through, which only gets further from now with time.
    """
    import json

    blob = json.dumps(build())
    assert "generatedAt" not in blob
    now = datetime.now(timezone.utc)
    for stamp in re.findall(r"20\d\d-\d\d-\d\dT\d\d:\d\d", blob):
        moment = datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)
        assert abs((now - moment).total_seconds()) > 3600, (
            f"{stamp} is within an hour of now, so it was read off the clock "
            f"rather than written down")


def test_the_fixture_is_byte_identical_across_builds():
    """The property the timestamp guard is a proxy for.

    A fixture that differs between builds makes CI's staleness check
    permanently red, and the pattern above cannot tell a wall clock from a
    constant. This can.
    """
    import json

    assert json.dumps(build(), sort_keys=True) == json.dumps(build(), sort_keys=True)


@pytest.mark.parametrize("key", sorted(BY_KEY), ids=lambda k: k)
def test_the_rendering_contract_the_surface_relies_on(key):
    """The surface branches on `treat` and then reads `absLift` and `mde`.

    It crashed the first time it met live data, because a program with no
    outcomes carried a rate of 0.00 and a null lift: the guard passed and the
    next line called toFixed on null. The contract is stated here rather than
    left implicit in the JavaScript, because the JavaScript is the thing that
    breaks when it is violated.
    """
    p = BY_KEY[key]
    if p["treat"] is None:
        assert p["ctrl"] is None and p["absLift"] is None and p["mde"] is None
        assert p["significant"] is False and p["pipeline"] is None
        return
    assert p["ctrl"] is not None, "a treatment rate without a control rate cannot be rendered"
    assert p["absLift"] is not None
    if p["mde"] is None:
        assert p["significant"] is False and p["pipeline"] is None
    if p["significant"]:
        assert p["mde"] is not None and p["absLift"] > p["mde"]
    # A significant *conversion* lift does not buy a euro figure: pipeline
    # rests on the opportunity comparison and on the tenant's own deals
    # (decision 39), so the two move independently and the absent one always
    # carries its reason.
    if p["pipeline"] is None:
        assert p["pipelineWithheld"], "no pipeline figure and no reason for it"
    else:
        assert p["pipeline"] > 0 and p["incrementalOpportunities"] > 0
        assert p["avgOpportunityEur"] > 0


# -- the sending fleet the demo shows ------------------------------------

def test_the_demo_fleet_is_computed_by_the_reference_core():
    """Every figure on the demo's Sending panel comes out of
    `zolts.deliverability`, not out of somebody's head.

    A demo that shows a capacity the product would not compute is a demo that
    lies about the one control a buyer's deliverability lead asks about, and
    it is the panel where the lie would be hardest to spot: 40 a day looks as
    plausible as 48.
    """
    from zolts.deliverability import BASE_CAP, warmup_factor

    fleet = FIXTURE["fleet"]
    domain = fleet["domains"][0]
    assert fleet["capacity"] == sum(m["capacity"] for m in domain["mailboxes"])

    for shown in domain["mailboxes"]:
        assert shown["remaining"] == max(0, shown["capacity"] - shown["sentToday"])
        # The warm-up curve is the half of the calculation that does not depend
        # on metrics the fixture does not carry, and it is the half a typed
        # number would get wrong: day six is not most of a mailbox.
        ceiling = BASE_CAP * warmup_factor(shown["warmupDay"]) * 1.2
        assert shown["capacity"] <= ceiling, (
            f"{shown['address']} shows {shown['capacity']} on warm-up day "
            f"{shown['warmupDay']}, above anything the curve produces")


def test_the_demo_fleet_shows_the_mechanism_and_not_a_happy_path():
    """A panel where everything is green demonstrates nothing.

    The fixture carries a mailbox mid-warm-up and one in alarm on purpose: they
    are what make the control visible. If a later edit smooths them away the
    demo still renders and stops proving anything, which is exactly the kind of
    regression nobody notices.
    """
    boxes = FIXTURE["fleet"]["domains"][0]["mailboxes"]
    assert any(b["warmupDay"] < 14 for b in boxes), "no mailbox is still warming up"
    assert any(b["health"] != "ok" for b in boxes), "every mailbox is healthy"
    assert {b["provider"] for b in boxes} >= {"google", "microsoft"}, (
        "the panel does not show per-provider segregation")


# -- the acquisition cost the demo shows ----------------------------------

def test_every_frozen_report_in_the_demo_prices_a_meeting_or_says_why():
    """Decision 45's figure, on the demo's own numbers. Neither state is
    hidden: one program declares a ceiling and has no figure to measure against
    it yet, the other has a figure and declares no ceiling. A demo that showed
    only the flattering half would be a demo of a different product.
    """
    seen = set()
    for program in FIXTURE["programs"]:
        for report in program.get("reports") or []:
            seen.add(report["costPerMeetingEur"] is None)
            if report["costPerMeetingEur"] is None:
                assert report["costPerMeetingWithheld"], (
                    f"{program['key']}: no cost per meeting and no reason for it")
                assert report["overCostCeiling"] is None, (
                    "no figure may not read as within budget")
            else:
                assert report["costPerMeetingEur"] > 0
                assert report["costPerMeetingWithheld"] is None
                ceiling = report["costPerMeetingCeilingEur"]
                assert report["overCostCeiling"] is (
                    None if ceiling is None
                    else report["costPerMeetingEur"] > ceiling)
    assert seen == {True, False}, (
        "the demo must show both the reported figure and the withheld one")


def test_the_demo_s_declared_ceiling_is_the_one_in_the_shipped_program():
    """The panel's ceiling is the program's, converted, and not a number typed
    into the fixture."""
    import yaml

    from zolts.report import ceiling_micros

    root = Path(__file__).resolve().parent.parent
    declared = {}
    for path in sorted((root / "examples" / "programs").glob("*.yaml")):
        document = yaml.safe_load(path.read_text())
        declared[document["metadata"]["key"]] = ceiling_micros(document["spec"])

    for program in FIXTURE["programs"]:
        for report in program.get("reports") or []:
            micros = declared[program["key"]]
            assert report["costPerMeetingCeilingEur"] == (
                None if micros is None else round(micros / 1_000_000, 2))
