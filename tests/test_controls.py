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

import json
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
        if control.surface != "program":
            continue  # the blueprint schema is a different document; see below
        assert control.path.startswith("spec."), control.path
        assert _describes(SCHEMA["properties"]["spec"], control.path.split(".")[1:]), (
            f"{control.path} names a field the program schema does not describe")


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


# -- the second surface: what a blueprint declares (D-64) --------------------

BLUEPRINTS = sorted(Path("blueprints").glob("*.yaml"))
BLUEPRINT_SCHEMA = json.loads(
    Path("examples/schema/zolts-blueprint.schema.json").read_text())


def test_every_blueprint_control_names_a_field_that_schema_describes():
    """The blueprint schema, not the program one. They share the name
    `spec.policy` and describe different things, which is exactly why the
    registry records which surface an entry belongs to."""
    for control in controls.CONTROLS:
        if control.surface != "blueprint":
            continue
        node = BLUEPRINT_SCHEMA["properties"]["spec"]
        parts = control.path.split(".")[1:]
        assert _describes(node, parts), (
            f"{control.path} is registered against the blueprint schema, which "
            f"does not describe it")


def test_the_regulated_blueprints_are_the_ones_that_trip_it():
    """The finding, asserted rather than described.

    `healthtech-lifesci` states `special_category_inference: forbidden` and
    `copy_approval_required: true`; `fintech-regulated` and `public-sector`
    state the second. Nothing reads any of them: `catalog.py` carries the whole
    policy block into the overlay and the gate consults one key, which is not
    one of these. A reader who opens those files — a partner's DPO, a buyer in
    diligence — reads a guarantee.
    """
    tripped = {}
    for path in BLUEPRINTS:
        spec = yaml.safe_load(path.read_text())["spec"]
        names = {c.path.split(".")[-1] for c in controls.unenforced(spec, "blueprint")}
        if names:
            tripped[path.stem] = names

    assert "special_category_inference" in tripped.get("healthtech-lifesci", set()), (
        "the archetype for health data no longer declares the prohibition, or the "
        "registry stopped seeing it")
    assert "copy_approval_required" in tripped.get("fintech-regulated", set())
    assert "copy_approval_required" in tripped.get("public-sector", set())


def test_a_blueprint_control_is_not_matched_against_a_program():
    """`spec.policy.copy_approval_required` is a blueprint field. Matching it
    against a program would report a limit that document could not declare, and
    a register full of impossible entries is one nobody reads."""
    pretend = {"policy": {"copy_approval_required": True}}
    assert controls.unenforced(pretend, "program") == []
    assert [c.path for c in controls.unenforced(pretend, "blueprint")] == [
        "spec.policy.copy_approval_required"]


# -- an override the schema does not name is refused, not ignored (D-65) -----

PROGRAM_SCHEMA = json.loads(
    Path("examples/schema/zolts-program.schema.json").read_text())


def _accepts(overrides: dict) -> bool:
    import copy

    import jsonschema

    document = yaml.safe_load(PROGRAMS[0].read_text())
    document = copy.deepcopy(document)
    document["spec"]["policy"]["overrides"] = overrides
    return not list(jsonschema.Draft202012Validator(PROGRAM_SCHEMA).iter_errors(document))


def test_a_misspelt_tightening_override_is_refused_rather_than_ignored():
    """The case this is for, and it runs the wrong way.

    `max_touches_per_week` is one word short of `max_touches_per_person_per_week`.
    It used to validate, and `gate.py` then applied its default of 3 to a program
    that had asked for 1 — three times the contact pressure the customer wrote,
    silently, on the axis a regulator cares about. A stricter override that does
    not take is worse than none, because the operator believes it did.
    """
    assert _accepts({"max_touches_per_person_per_week": 1}), (
        "the real name must still be accepted, or this test proves only that the "
        "block is closed")
    assert not _accepts({"max_touches_per_week": 1}), (
        "a misspelt override is accepted and silently ignored")


def test_the_four_overrides_the_runtime_reads_are_all_accepted():
    """Closing the block is only safe if it is closed around the right set.
    Each of these is read: `gate.py` for the touch cap, `zolts/policy.py` for
    the other three."""
    for override in ({"quiet_hours": {"start": "20:00", "end": "08:00"}},
                     {"max_touches_per_person_per_week": 2},
                     {"channels_require_basis": {"email": "consent"}},
                     {"lists_check": ["robinson_list_es"]}):
        assert _accepts(override), f"{list(override)[0]} is read by the runtime and refused"


def test_no_shipped_program_declares_an_override_the_schema_does_not_name():
    """`03-ecommerce-dtc` declared `discount_authority` here — commercial
    latitude, not a policy override, accepted only because the block was open.
    It belongs to the blueprint, where it is registered as unenforced."""
    named = set(PROGRAM_SCHEMA["properties"]["spec"]["properties"]["policy"]
                ["properties"]["overrides"]["properties"])
    for path in PROGRAMS:
        overrides = ((yaml.safe_load(path.read_text())["spec"].get("policy") or {})
                     .get("overrides") or {})
        unknown = set(overrides) - named
        assert not unknown, f"{path.name} declares {sorted(unknown)}, which nothing reads"


# -- a flag that advertises a choice which does not exist (D-66) -------------

def test_the_only_way_to_supply_a_secret_is_stdin_whether_the_flag_is_passed_or_not():
    """`--secret-stdin` was `store_true` with `default=True`: never false, and
    read by nothing. A flag naming a mode implies another mode, and an operator
    following `docs/26` under pressure may reasonably look for `--secret-file`.
    There is none, deliberately. The flag is accepted so three documented
    commands keep working, and its help says it selects nothing.

    Asserted on the parser rather than by running `connect`, which would need a
    database and a sealing key to prove a property of the interface.
    """
    from runtime import cli

    parser = cli.build_parser() if hasattr(cli, "build_parser") else None
    if parser is None:
        import inspect
        source = inspect.getsource(cli)
        assert 'conn.add_argument("--secret-stdin"' in source
        assert "accepted and ignored" in source, (
            "the flag no longer says that it selects nothing")
        assert "args.secret_stdin" not in source, (
            "something now reads the flag; give it a real alternative mode or "
            "take the word 'ignored' out of its help")
