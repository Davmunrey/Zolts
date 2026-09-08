"""Which limits a program declares, and which the runtime actually holds.

`spec.budget` promised a program-level spending regime and delivered one field
of five, for one of the eight priced actions (D-63). Nothing failed, because
the *tenant* ceiling does fire and defers actions with `budget: ...` — so an
operator watching budget enforcement work had every reason to believe the
program's own block was what worked.

A limit nothing reads fails in the flattering direction: the customer wrote a
ceiling, nothing exceeded it, and the system looks correct. These assert that
the registry naming every control is complete, that its paths are real, and
that the console never offers a dial the runtime does not honour.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zolts import controls, dsl

SCHEMA = dsl.load_schema()
PROGRAMS = sorted(Path("examples/programs").glob("*.yaml"))


def _deref(node: dict) -> dict:
    while "$ref" in node:
        ref = node["$ref"].split("/")[-1]
        node = (SCHEMA.get("$defs") or SCHEMA.get("definitions") or {})[ref]
    return node


def _describes(node: dict, parts: list[str]) -> bool:
    """Whether the schema describes a field at a *spec* path.

    `dsl._resolve` walks `properties` alone, which is right for the tunable set
    — every dial is a scalar under an object — and cannot answer this, because
    `declared_paths` collapses list elements. A field of one tier reaches the
    registry as `spec.route.tiers.capacity_per_week`, with no index, so this
    steps through `items` too. Reconciling the two shapes here is the point: the
    alternative is writing the registry in a shape no program ever has, which is
    how its paths were wrong the first time.

    `*` stands for a block chosen by name — `enrich.account` or `enrich.person`,
    a play keyed by its tier — and matches if any one of them resolves the rest.
    """
    node = _deref(node)
    while node.get("type") == "array" or "items" in node:
        node = _deref(node["items"])
    if not parts:
        return True
    head, rest = parts[0], parts[1:]
    properties = node.get("properties") or {}
    if head == "*":
        candidates = list(properties.values())
        extra = node.get("additionalProperties")
        if isinstance(extra, dict):
            candidates.append(extra)
        return any(_describes(c, rest) for c in candidates)
    if head not in properties:
        return False
    return _describes(properties[head], rest)


def test_every_registered_control_names_a_field_the_schema_describes():
    """A registry entry for a field that does not exist is not a record, it is
    a reassurance."""
    for control in controls.CONTROLS:
        assert control.path.startswith("spec."), control.path
        assert _describes(SCHEMA["properties"]["spec"], control.path.split(".")[1:]), (
            f"{control.path} names a field the schema does not describe")


def test_the_schema_check_refuses_a_field_that_is_not_there():
    """Otherwise the test above passes for every string ever written."""
    spec = SCHEMA["properties"]["spec"]
    assert not _describes(spec, ["budget", "invented_ceiling"])
    assert not _describes(spec, ["route", "tiers", "invented_capacity"])
    assert _describes(spec, ["route", "tiers", "capacity_per_week"]), (
        "the walker cannot see through a list, which is the case it exists for")


def test_every_control_the_runtime_does_not_hold_says_what_that_costs():
    """`docs/22` asks for the consequence, not the recommendation. An entry
    that names a gap without saying what a customer loses cannot be triaged."""
    for control in controls.NOT_HONOURED:
        assert len(control.consequence) > 40, (
            f"{control.path} is unenforced and does not say what that costs")


def test_every_control_the_runtime_holds_says_where():
    """A claim of enforcement that cannot be checked is the thing this registry
    exists to replace."""
    for control in controls.HONOURED:
        assert "/" in control.enforced_by or "." in control.enforced_by, (
            f"{control.path} claims enforcement without naming where")


def test_the_console_never_offers_a_dial_the_runtime_does_not_honour():
    """The rule that makes D-63 a class rather than an incident.

    `spec.budget.on_exceed` was a tunable offering three behaviours nothing
    distinguished. An operator who sets it gets a saved program, a new version,
    and identical behaviour — a control they will believe they have. Whatever
    else a dial may be, it may not be decoration.
    """
    tunable = {path for path, _ in dsl.TUNABLE}
    unenforced = {c.path for c in controls.NOT_HONOURED}
    offered_but_dead = tunable & unenforced
    assert not offered_but_dead, (
        f"the console offers {sorted(offered_but_dead)}, which the runtime does not "
        f"honour. Enforce it, or take it out of the tunable set")


@pytest.mark.parametrize("path", [p for p in PROGRAMS], ids=lambda p: p.name)
def test_each_shipped_program_reports_the_limits_that_are_decoration(path):
    """Not that there are none — there are, and they are registered. That the
    reader of a program can find out which, rather than assuming all of them
    hold."""
    spec = yaml.safe_load(path.read_text())["spec"]
    missing = controls.unenforced(spec)
    for control in missing:
        assert control.path in controls.BY_PATH
        assert not control.honoured


def test_the_registry_matches_real_program_paths_and_not_only_itself():
    """The failure this test exists for happened while it was being written.

    The patterns were first written as though `declared_paths` indexed list
    elements — `spec.route.tiers.*.capacity_per_week`. Nothing in any program
    has that shape, so the registry matched nothing and reported every program
    clean: the exact defect it was built to catch, one level up. A registry that
    can silently match nothing is worse than none, so this asserts the shipped
    programs do trip it.
    """
    seen: set[str] = set()
    for path in PROGRAMS:
        spec = yaml.safe_load(path.read_text())["spec"]
        seen.update(c.path for c in controls.unenforced(spec))
    assert len(seen) >= 5, (
        f"only {sorted(seen)} matched across every shipped program; the registry's "
        f"paths are probably shaped wrong")
    assert "spec.route.tiers.capacity_per_week" in seen, (
        "a field inside a list of tiers is not being matched, which is how the "
        "patterns were wrong the first time")


def test_a_program_declaring_nothing_reports_nothing():
    """The empty case, so the list is evidence rather than noise."""
    assert controls.unenforced({"experiment": {"holdout_pct": 10}}) == []
