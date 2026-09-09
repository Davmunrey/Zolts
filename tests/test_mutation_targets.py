"""The mutation list has to still point at the code it was written for.

`scripts/mutation_check.py` breaks a guard on purpose and requires a test to
notice. Every one of its mutations names an exact string in an exact file, and
the day that string moves the mutation stops checking anything. The script
reports that as an error rather than a skip — but the script is a deliberate
command, and this is the per-commit version of the same question.

Milliseconds, no mutations applied, no tests run.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_module():
    """Loaded by path, so `scripts/` stays a directory of scripts rather than
    becoming an importable package for one test's convenience."""
    spec = importlib.util.spec_from_file_location(
        "mutation_check", ROOT / "scripts" / "mutation_check.py")
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: `@dataclass` resolves its own module out of
    # `sys.modules`, and a module executed without being registered there
    # fails on the decorator rather than on anything to do with this test.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECK = _load_module()
MUTATIONS = CHECK.MUTATIONS


@pytest.mark.parametrize("mutation", MUTATIONS, ids=[m.id for m in MUTATIONS])
def test_the_mutation_still_has_something_to_break(mutation):
    source = (ROOT / mutation.path).read_text()
    assert mutation.find in source, (
        f"'{mutation.id}' breaks a line that is no longer in {mutation.path}, so the "
        f"guard for '{mutation.claim}' is not being checked by anything")


@pytest.mark.parametrize("mutation", MUTATIONS, ids=[m.id for m in MUTATIONS])
def test_the_mutation_names_tests_that_exist(mutation):
    assert (ROOT / mutation.tests).exists(), (
        f"'{mutation.id}' expects {mutation.tests} to fail, and that file is gone")


def test_the_mutation_changes_something():
    """A mutation whose replacement equals its target is a check that passes
    because it changed nothing."""
    for mutation in MUTATIONS:
        assert mutation.find != mutation.replace, mutation.id


def test_every_mutation_states_the_claim_it_defends():
    """The list is only useful if each entry says what would be lost. An id is
    a label; the claim is the reason somebody should care that it survived."""
    for mutation in MUTATIONS:
        assert len(mutation.claim.split()) >= 6, (
            f"'{mutation.id}' does not say what it defends")


# -- a skipped test is not a result about the code ------------------------

def _summary(passed: int = 0, failed: int = 0, skipped: int = 0) -> str:
    """A pytest terse summary line, the way the script reads one."""
    parts = ([f"{failed} failed"] if failed else []) \
        + ([f"{passed} passed"] if passed else []) \
        + ([f"{skipped} skipped"] if skipped else [])
    return f"{', '.join(parts)} in 1.23s\n"


def _outcome(monkeypatch, *, returncode: int, stdout: str) -> str:
    """Run the classifier against a stubbed pytest, so the premise the test
    asserts about is the one it established."""
    class _Result:
        pass

    result = _Result()
    result.returncode, result.stdout = returncode, stdout
    monkeypatch.setattr(CHECK.subprocess, "run", lambda *a, **k: result)
    return CHECK._run_tests("tests/whatever.py")


def test_a_target_whose_tests_all_skipped_is_not_a_surviving_guard(monkeypatch):
    """D-71. Eight of these guards live in `requires_db` tests, and a skip
    exits zero exactly like a pass. Run without a database the script called
    them survivors and printed `10/18 guards bite` — a red result about code
    that was never wrong, which is the third shape this register has named of a
    check that fails on its environment rather than its subject.
    """
    assert _outcome(monkeypatch, returncode=0,
                    stdout=_summary(skipped=17)) == CHECK.NOTHING_RAN
    assert _outcome(monkeypatch, returncode=5, stdout="no tests ran in 0.01s\n") \
        == CHECK.NOTHING_RAN


def test_a_real_pass_and_a_real_failure_are_still_told_apart(monkeypatch):
    """Otherwise the fix above turns every guard into `not checked`."""
    assert _outcome(monkeypatch, returncode=0,
                    stdout=_summary(passed=12, skipped=3)) == CHECK.PASSED
    assert _outcome(monkeypatch, returncode=1,
                    stdout=_summary(failed=1, skipped=3)) == CHECK.FAILED
    # `-x` stops at the first failure, so a biting mutation reports one failure
    # and a pile of tests that never ran. Still a bite.
    assert _outcome(monkeypatch, returncode=2,
                    stdout=_summary(failed=1)) == CHECK.FAILED


def test_the_script_says_what_it_could_not_check_rather_than_counting_it():
    """A count over guards nothing ran is the number that misleads. The
    denominator is what was checked, and the rest is named."""
    source = (ROOT / "scripts" / "mutation_check.py").read_text()
    assert "guards nothing checked, because their tests all skipped" in source
    assert "not checked" in source
    assert "checked = len(chosen) - len(unchecked)" in source
