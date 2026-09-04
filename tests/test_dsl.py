"""Program loading, hashing and static lint over the shipped examples."""

from pathlib import Path

import pytest
import yaml

from zolts.dsl import Program, ProgramError, lint, load

PROGRAM_DIR = Path(__file__).resolve().parent.parent / "examples" / "programs"
PROGRAMS = sorted(PROGRAM_DIR.glob("*.yaml"))


def test_examples_exist():
    assert len(PROGRAMS) == 4


@pytest.mark.parametrize("path", PROGRAMS, ids=lambda p: p.stem)
def test_example_loads_and_validates(path):
    program = load(path)
    assert program.key and program.semver


@pytest.mark.parametrize("path", PROGRAMS, ids=lambda p: p.stem)
def test_example_passes_lint(path):
    findings = lint(load(path))
    assert findings == [], f"{path.name}: {findings}"


@pytest.mark.parametrize("path", PROGRAMS, ids=lambda p: p.stem)
def test_every_example_declares_a_real_holdout(path):
    """The product invariant: measurement is not optional."""
    program = load(path)
    assert program.holdout_pct >= 5


def test_spec_hash_is_stable_and_key_order_independent():
    program = load(PROGRAMS[0])
    reordered = Program(
        key=program.key,
        version=program.version,
        spec=dict(reversed(list(program.spec.items()))),
        raw=program.raw,
    )
    assert program.spec_hash == reordered.spec_hash


def test_spec_hash_changes_with_content():
    program = load(PROGRAMS[0])
    mutated = dict(program.spec)
    mutated["budget"] = dict(mutated["budget"], monthly_credits=1)
    other = Program(program.key, program.version, mutated, program.raw)
    assert program.spec_hash != other.spec_hash


def test_boolean_trigger_key_is_rejected_with_a_clear_message(tmp_path):
    """Regression guard for the YAML 1.1 `on:` coercion trap."""
    source = yaml.safe_load(PROGRAMS[0].read_text())
    source["spec"]["trigger"][True] = source["spec"]["trigger"].pop("events")
    broken = tmp_path / "broken.yaml"
    broken.write_text(yaml.safe_dump(source))
    with pytest.raises(ProgramError, match="coerced `on:`"):
        load(broken)


def test_lint_flags_tier_one_auto_send_in_a_one_to_one_motion():
    source = yaml.safe_load(PROGRAMS[0].read_text())  # b2b-saas-sales-led
    source["spec"]["plays"]["t1"]["auto_send"] = True
    source["spec"]["plays"]["t1"]["auto_send_requires"] = {"eval_score": 0.9}
    program = Program("x", "1.0.0", source["spec"], source)
    assert any("1:1 motion must never auto-send" in f for f in lint(program))


def test_lint_allows_tier_one_auto_send_in_a_high_volume_b2c_motion():
    """The tier-1 rule is scoped by blueprint: in B2C, t1 means high LTV, not
    human-touched, and reviewing every message by hand is not a real option."""
    source = yaml.safe_load(
        (PROGRAM_DIR / "03-ecommerce-dtc.yaml").read_text()
    )
    program = Program("x", "1.0.0", source["spec"], source)
    assert not any("auto-send" in f for f in lint(program))


def test_unknown_blueprint_defaults_to_requiring_human_review():
    """The safe failure mode: an unnecessary review costs less than a bad send."""
    source = yaml.safe_load(PROGRAMS[0].read_text())
    source["metadata"].pop("blueprint", None)
    source["spec"]["plays"]["t1"]["auto_send"] = True
    source["spec"]["plays"]["t1"]["auto_send_requires"] = {"eval_score": 0.9}
    program = Program("x", "1.0.0", source["spec"], source)
    assert any("1:1 motion must never auto-send" in f for f in lint(program))


def test_auto_send_without_an_eval_gate_is_flagged_in_every_motion():
    for path in PROGRAMS:
        source = yaml.safe_load(path.read_text())
        tier = next(iter(source["spec"]["plays"]))
        source["spec"]["plays"][tier]["auto_send"] = True
        source["spec"]["plays"][tier].pop("auto_send_requires", None)
        program = Program("x", "1.0.0", source["spec"], source)
        assert any("eval_score threshold" in f for f in lint(program)), path.name


def test_lint_flags_a_route_tier_with_no_play(tmp_path):
    source = yaml.safe_load(PROGRAMS[0].read_text())
    source["spec"]["route"]["tiers"].append({"key": "t9", "when": "score >= 0"})
    program = Program("x", "1.0.0", source["spec"], source)
    assert any("dead end" in f for f in lint(program))


def test_lint_flags_a_sub_five_percent_holdout_without_a_waiver():
    source = yaml.safe_load(PROGRAMS[0].read_text())
    source["spec"]["experiment"]["holdout_pct"] = 2
    program = Program("x", "1.0.0", source["spec"], source)
    assert any("waiver" in f for f in lint(program))
