"""Cross-references between programs and blueprints.

Neither schema can see the other, so every reference across the boundary is
unchecked by construction. Three of the failures this catches — an unarmed
signal, a dropped suppression list, a holdout below the blueprint's floor — are
compliance failures that produce no error at runtime.
"""

import pytest

from zolts.blueprint import load_blueprints
from zolts.catalog import Catalog, Issue, integrity, load_catalog
from zolts.dsl import Program

CATALOG = load_catalog()


def test_catalog_loads_both_sides():
    assert CATALOG.programs and CATALOG.blueprints


def test_shipped_catalog_is_clean():
    issues = integrity(CATALOG)
    assert issues == [], [str(i) for i in issues]


@pytest.mark.parametrize("program", CATALOG.programs, ids=lambda p: p.key)
def test_every_program_resolves_to_a_real_blueprint(program):
    assert CATALOG.blueprint_for(program) is not None


@pytest.mark.parametrize("program", CATALOG.programs, ids=lambda p: p.key)
def test_every_program_is_declared_by_its_blueprint(program):
    assert program.key in CATALOG.blueprint_for(program).programs


@pytest.mark.parametrize("program", CATALOG.programs, ids=lambda p: p.key)
def test_every_trigger_signal_is_armed_by_the_blueprint(program):
    armed = set(CATALOG.blueprint_for(program).signals)
    used = {e["signal"] for e in program.spec["trigger"]["events"]}
    assert used <= armed, f"{sorted(used - armed)} not armed by the blueprint"


def _mutate(program: Program, mutator) -> Catalog:
    spec = {k: (dict(v) if isinstance(v, dict) else v) for k, v in program.spec.items()}
    mutator(spec)
    raw = dict(program.raw)
    return Catalog(
        programs=[Program(program.key, program.version, spec, raw)],
        blueprints={b.key: b for b in load_blueprints()},
    )


def _local() -> Program:
    return next(p for p in CATALOG.programs if p.key == "new-site-and-reputation")


def test_dropping_a_blueprint_suppression_list_is_caught():
    """The regression this module was written for. A program that replaces the
    blueprint's lists instead of adding to them makes opted-out contacts,
    current customers and open opportunities reachable again — silently."""
    program = _local()

    def drop(spec):
        policy = dict(spec["policy"])
        overrides = dict(policy["overrides"])
        overrides["lists_check"] = ["robinson_list_es"]
        policy["overrides"] = overrides
        spec["policy"] = policy

    issues = integrity(_mutate(program, drop))
    assert any(i.kind == "policy-loosened" for i in issues)
    assert "global_suppression" in str(issues[0])


def test_weakening_a_required_basis_is_caught():
    program = _local()

    def weaken(spec):
        policy = dict(spec["policy"])
        overrides = dict(policy["overrides"])
        overrides["channels_require_basis"] = {"whatsapp": "legitimate_interest"}
        policy["overrides"] = overrides
        spec["policy"] = policy

    assert any(i.kind == "policy-loosened" for i in integrity(_mutate(program, weaken)))


def test_raising_the_frequency_cap_above_the_blueprint_is_caught():
    program = _local()

    def raise_cap(spec):
        policy = dict(spec["policy"])
        overrides = dict(policy["overrides"])
        overrides["max_touches_per_person_per_week"] = 9
        policy["overrides"] = overrides
        spec["policy"] = policy

    assert any(i.kind == "policy-loosened" for i in integrity(_mutate(program, raise_cap)))


def test_an_unarmed_signal_is_caught():
    program = _local()

    def swap(spec):
        trigger = dict(spec["trigger"])
        trigger["events"] = [{"signal": "intent.topic_surge"}]
        spec["trigger"] = trigger

    assert any(i.kind == "unarmed-signal" for i in integrity(_mutate(program, swap)))


def test_a_holdout_below_the_blueprint_floor_is_caught():
    program = _local()

    def shrink(spec):
        experiment = dict(spec["experiment"])
        experiment["holdout_pct"] = 5
        spec["experiment"] = experiment

    assert any(i.kind == "holdout-below-blueprint" for i in integrity(_mutate(program, shrink)))


def test_an_unknown_blueprint_is_caught():
    program = _local()
    raw = dict(program.raw)
    raw["metadata"] = {**raw["metadata"], "blueprint": "does-not-exist"}
    catalog = Catalog(
        programs=[Program(program.key, program.version, program.spec, raw)],
        blueprints={b.key: b for b in load_blueprints()},
    )
    assert any(i.kind == "unknown-blueprint" for i in integrity(catalog))


def test_planned_programs_are_tracked_not_treated_as_errors():
    """A blueprint promising more programs than ship today is a roadmap, not a
    defect — but the gap should be visible rather than invisible."""
    assert CATALOG.planned_programs
    assert not (CATALOG.planned_programs & {p.key for p in CATALOG.programs})


def test_issue_renders_readably():
    assert "x: kind — detail" == str(Issue("x", "kind", "detail"))
