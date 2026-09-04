"""The declarative runner must actually fail when a program breaks compliance.

docs/04 claims "a program change that breaks a compliance rule cannot be
merged". That claim is worth nothing unless a broken program genuinely fails
the suite, so these tests break programs on purpose and assert the failure.
"""

from pathlib import Path

import pytest
import yaml

from zolts.dsl import Program, load
from zolts.programtest import run_case, run_suite

PROGRAM_DIR = Path(__file__).resolve().parent.parent / "examples" / "programs"
TEST_DIR = Path(__file__).resolve().parent.parent / "examples" / "tests"
SUITES = sorted(TEST_DIR.glob("*.test.yaml"))


def _program_for(suite_path: Path) -> Program:
    return load(PROGRAM_DIR / suite_path.name.replace(".test.yaml", ".yaml"))


def test_suites_exist():
    assert SUITES


@pytest.mark.parametrize("suite_path", SUITES, ids=lambda p: p.stem)
def test_shipped_suites_pass(suite_path):
    result = run_suite(_program_for(suite_path), suite_path)
    assert result.passed, [c.failures for c in result.cases if not c.passed]


def test_a_relaxed_tier_threshold_fails_the_suite():
    """Lowering the tier 1 threshold reroutes an account the suite pins to t1."""
    program = _program_for(SUITES[0])
    broken = dict(program.spec)
    broken["route"] = dict(broken["route"])
    broken["route"]["tiers"] = [
        {"key": "t1", "when": "score >= 95", "capacity_per_week": 25},
        *program.spec["route"]["tiers"][1:],
    ]
    result = run_suite(Program(program.key, program.version, broken, program.raw), SUITES[0])
    assert not result.passed
    assert any("tier" in f for case in result.cases for f in case.failures)


def test_enabling_auto_send_on_tier_one_fails_the_suite():
    """The invariant an enterprise DPO is shown: tier 1 is human-reviewed."""
    program = _program_for(SUITES[0])
    broken = dict(program.spec)
    broken["plays"] = dict(broken["plays"])
    broken["plays"]["t1"] = {**broken["plays"]["t1"], "auto_send": True}
    result = run_suite(Program(program.key, program.version, broken, program.raw), SUITES[0])
    assert not result.passed
    assert any("auto_send" in f for case in result.cases for f in case.failures)


def test_widening_the_trigger_window_fails_the_suite():
    """A 90-day-old funding signal must not enrol under a 30-day window."""
    program = _program_for(SUITES[0])
    broken = dict(program.spec)
    broken["trigger"] = {**broken["trigger"], "window": "180d"}
    result = run_suite(Program(program.key, program.version, broken, program.raw), SUITES[0])
    assert not result.passed
    assert any("enrolled" in f for case in result.cases for f in case.failures)


def test_a_broken_qualifying_clause_fails_the_suite():
    """Dropping the funding-stage filter would enrol seed rounds."""
    program = _program_for(SUITES[0])
    broken = dict(program.spec)
    broken["trigger"] = dict(broken["trigger"])
    broken["trigger"]["events"] = [
        {"signal": e["signal"]} for e in program.spec["trigger"]["events"]
    ]
    result = run_suite(Program(program.key, program.version, broken, program.raw), SUITES[0])
    assert not result.passed


def test_a_failing_case_names_the_expectation_and_the_actual():
    """A failure a human cannot act on is a failure that gets ignored."""
    program = _program_for(SUITES[0])
    result = run_case(program, {
        "name": "deliberately wrong",
        "given": {"person": {"country": "ES", "consent_state": {"email": "legitimate_interest"}},
                  "channel": "email"},
        "expect": {"policy_decision": "deny"},
    })
    assert not result.passed
    assert "expected deny" in result.failures[0]
    assert "got allow" in result.failures[0]


def test_every_program_with_a_suite_covers_a_denial_case():
    """A suite that only asserts happy paths proves nothing about compliance."""
    for suite_path in SUITES:
        cases = yaml.safe_load(suite_path.read_text())["cases"]
        denials = [c for c in cases if c.get("expect", {}).get("policy_decision") == "deny"]
        assert denials, f"{suite_path.name} has no denial case"
