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


def _load_mutations():
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
    return module.MUTATIONS


MUTATIONS = _load_mutations()


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
