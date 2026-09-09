"""The probe that found four of the five environment-shaped defects, as a script.

`docs/22` names a shape it has five instances of: a check that fails on its
environment rather than on its subject. Each was found the same way — run the
thing twice under different ambient conditions and read the *difference* rather
than the second number — and each probe was ad hoc. None was in the repository.
That is where hand-verified guards stood before `mutation_check.py`, and it
ended the same way: the verification worked once and could not be repeated.

`scripts/ambient_check.py` is that probe. Its first run found fifteen D-73
entries a hand probe over three files had missed, which is the argument for it.

These tests do not run the suite — that is minutes per condition. They hold the
script's judgement: what counts as a disagreement, what counts as a test
honestly standing down, and that a condition is described well enough for
somebody reading the output to know what it did.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "ambient_check", ROOT / "scripts" / "ambient_check.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECK = _load()


def test_every_condition_says_what_it_varies_and_why():
    """A condition nobody can read is a result nobody can act on. Each names
    the defect it exists for, or the state it stands in for."""
    for condition in CHECK.CONDITIONS:
        assert condition.id and " " not in condition.id
        assert len(condition.why) > 40, f"{condition.id} does not say why it exists"
    assert len(CHECK.CONDITIONS) >= 4
    assert sum(1 for c in CHECK.CONDITIONS if c.default) == 2, (
        "the default pair is a baseline and one shift; more would make the "
        "command too slow to run and it would stop being run")


def test_the_baseline_is_the_first_condition_and_varies_nothing():
    baseline = CHECK.CONDITIONS[0]
    assert baseline.id == "baseline"
    assert baseline.env == {}, "the baseline is what everything else differs from"
    assert baseline.default


def test_a_condition_can_remove_a_variable_and_not_only_set_one():
    """D-73's condition is the *absence* of a variable, so None has to mean
    remove rather than set to the string 'None'."""
    absent = CHECK.BY_ID["no-app-role"]
    assert absent.env["ZOLTS_TEST_APP_DATABASE_URL"] is None


def test_the_outcome_pattern_reads_a_parametrised_node_id():
    """A node id can carry a suffix with spaces in it. The first version of the
    pattern stopped at the first space and matched the wrong thing."""
    line = ("tests/test_period_spend.py::test_a_declaration_is_refused"
            "[bad0-ends before] PASSED [  3%]")
    assert CHECK._OUTCOME.findall(line) == [
        ("tests/test_period_spend.py::test_a_declaration_is_refused[bad0-ends before]",
         "PASSED")]


def test_the_outcome_pattern_reads_every_verdict_pytest_can_give():
    for verdict in ("PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS"):
        assert CHECK._OUTCOME.findall(f"tests/a.py::b {verdict} [ 10%]") == [
            ("tests/a.py::b", verdict)]


def test_the_run_asks_pytest_for_the_per_test_lines():
    """`-q` silently wins over `-v`, and the first version passed both and
    matched nothing at all — reporting a clean run because it had parsed no
    runs. A check that reports success when it read nothing is worse than
    none."""
    source = (ROOT / "scripts" / "ambient_check.py").read_text()
    assert '"-v", "--no-header",' in source
    assert '"-q"' not in source, "-q suppresses the per-test lines this reads"
    assert "collected nothing" in source, "a run that parsed nothing must not read as clean"


def test_a_skip_is_not_counted_against_the_run_and_a_failure_is():
    """The judgement the whole script turns on. `no-app-role` removes a premise
    on purpose: the tests that then skip are answering it correctly, and a
    script that called that a defect would flag its own best condition."""
    source = (ROOT / "scripts" / "ambient_check.py").read_text()
    assert 'if "FAILED" in (was, now) or "ERROR" in (was, now):' in source
    assert "stood_down" in source, "a skip is reported, not counted"


@pytest.mark.parametrize("argv, expected", [
    (["--only", "baseline"], 2),           # one condition cannot differ from anything
    (["--only", "not-a-condition"], 2),    # a name that does not exist
])
def test_the_command_refuses_a_run_that_could_not_report_a_difference(argv, expected,
                                                                     monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ambient_check.py", *argv])
    assert CHECK.main() == expected


def test_the_error_message_names_the_defects_the_script_exists_for():
    """A count in a message drifts the same way a count in a document does,
    and this one is the first thing somebody reads when the script fires."""
    from pathlib import Path

    source = (ROOT / "scripts" / "ambient_check.py").read_text()
    register = (ROOT / "docs" / "22-defect-register.md").read_text()
    shape = next(ln for ln in register.splitlines()
                 if "check that fails on its environment rather than on its subject" in ln)
    assert "Six of them" in shape or "six of them" in shape, (
        "the register's count of this shape moved; the script's message quotes it")
    for defect in ("D-35", "D-59", "D-68", "D-71", "D-73", "D-74"):
        assert defect in source, f"{defect} is of this shape and the message omits it"
