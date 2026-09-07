"""The parameters a console may tune, and where their bounds come from.

ADR-002 says the DSL is the source of truth and the UI generates DSL. The
first half was enforced from the beginning — `POST /v1/programs` validates a
whole document against the schema, lints it and checks the holdout. The second
half had no implementation at all: the console was read-only, and nothing in
the repository emitted a program document.

A UI that edits a program has to know what a field accepts. The failure mode
is not that it looks wrong — it is that it offers a value the engine rejects,
and the operator finds out after pressing publish. So the controls carry the
schema's own constraints, read at call time. These tests hold that they are
read rather than restated.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pytest

from zolts import dsl

SCHEMA = dsl.load_schema()
CONTROLS = dsl.controls()


def test_every_tunable_path_exists_in_the_schema():
    """A control whose field the schema does not describe renders with no
    bounds at all, which is the one state worse than a missing control."""
    for path, _ in dsl.TUNABLE:
        dsl._resolve(SCHEMA, path)  # raises ProgramError if it does not resolve


def test_a_path_the_schema_does_not_describe_is_refused():
    with pytest.raises(dsl.ProgramError, match="no field at"):
        dsl._resolve(SCHEMA, "spec.experiment.invented_field")


def test_each_control_carries_the_schema_s_own_bounds():
    """Not a copy of them. If these were restated in the control table they
    would drift the first time the schema moved, and the UI would go on
    offering the old range."""
    for control in CONTROLS:
        node = dsl._resolve(SCHEMA, control["path"])
        for key in ("minimum", "maximum", "enum", "pattern"):
            assert control.get(key) == node.get(key), (
                f"{control['path']}: control says {control.get(key)!r}, "
                f"schema says {node.get(key)!r}")


def test_every_control_is_bounded_somehow():
    """An unbounded control is a free-text box on a live program's spend."""
    for control in CONTROLS:
        assert any(k in control for k in ("minimum", "enum", "pattern")), (
            f"{control['path']} accepts anything the type allows")


def test_every_control_says_what_getting_it_wrong_costs():
    """These are dials on a running program. A label alone leaves the operator
    guessing which direction is the expensive one."""
    for control in CONTROLS:
        assert len(control["note"]) > 30, f"{control['path']} has no consequence"


def test_the_tunable_set_is_the_money_and_risk_dials():
    """Deliberately not "every field the schema allows". Editing a trigger's
    events or an audience's SQL from a console is a different act with a
    different blast radius, and it belongs in a pull request."""
    paths = {path for path, _ in dsl.TUNABLE}
    assert "spec.experiment.holdout_pct" in paths, "the holdout is the first dial"
    assert "spec.audience.sql" not in paths, (
        "audience SQL is not a dial; changing who is enrolled from a console "
        "is a code review, not a form")
    assert not any(p.startswith("spec.plays") for p in paths), (
        "play copy is not a dial either")


def test_a_document_built_from_the_controls_validates():
    """The point of the exercise: apply every control at a legal value to a
    real program and the result is still a document the engine accepts."""
    programs = sorted(Path("examples/programs").glob("*.yaml"))
    assert programs, "no example programs to edit"
    program = dsl.load(programs[0])
    document = json.loads(json.dumps(program.raw))

    for control in CONTROLS:
        value = _a_legal_value(control)
        _set(document, control["path"], value)

    jsonschema.Draft202012Validator(SCHEMA).validate(document)


def test_a_document_built_past_a_bound_is_refused():
    """The bounds are real, not decoration. A holdout of 80 is outside the
    schema's 0-50 and must fail before it reaches a tenant."""
    programs = sorted(Path("examples/programs").glob("*.yaml"))
    document = json.loads(json.dumps(dsl.load(programs[0]).raw))
    _set(document, "spec.experiment.holdout_pct", 80)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(SCHEMA).validate(document)


def _a_legal_value(control: dict) -> object:
    if "enum" in control:
        return control["enum"][0]
    if "pattern" in control:
        # The two string controls are durations; both patterns accept `7d`.
        assert re.match(control["pattern"], "7d"), control["path"]
        return "7d"
    low = control.get("minimum", 0)
    high = control.get("maximum", low + 10)
    value = (low + high) / 2
    return int(value) if control["type"] == "integer" else value


def _set(document: dict, path: str, value: object) -> None:
    node = document
    parts = path.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


# -- a version is written once --------------------------------------------

@pytest.mark.db
def test_republishing_a_version_with_different_content_is_refused(db, tenant):
    """`publish` was a plain upsert, so publishing v2.1.0 twice replaced the
    spec of a version that was already live.

    Every enrollment running under it then pointed at a document that no
    longer described what it did, and the measurement attributed one lift to
    two different programs. Program logic is versioned configuration; a
    version is written once.
    """
    from runtime.repo import programs

    with db.tenant_tx(tenant['id']) as cur:
        first = programs.publish(cur, tenant['id'], key="immutable-demo", version="1.0.0",
                                 spec={"a": 1}, spec_hash="hash-one")
        assert first["spec"] == {"a": 1}

        with pytest.raises(programs.VersionIsImmutable, match="already exists"):
            programs.publish(cur, tenant['id'], key="immutable-demo", version="1.0.0",
                             spec={"a": 2}, spec_hash="hash-two")

    with db.tenant_tx(tenant['id']) as cur:
        cur.execute("select spec from program where key = %s and version = %s",
                    ("immutable-demo", "1.0.0"))
        assert cur.fetchone()["spec"] == {"a": 1}, "the stored spec was replaced"


@pytest.mark.db
def test_republishing_identical_content_still_succeeds(db, tenant):
    """A retried request and a re-seeded starter program are both legitimate,
    and neither changes anything. Refusing them would make the API's own
    idempotency a failure."""
    from runtime.repo import programs

    with db.tenant_tx(tenant['id']) as cur:
        programs.publish(cur, tenant['id'], key="idempotent-demo", version="1.0.0",
                         spec={"a": 1}, spec_hash="same-hash")
        again = programs.publish(cur, tenant['id'], key="idempotent-demo", version="1.0.0",
                                 spec={"a": 1}, spec_hash="same-hash",
                                 metadata={"name": "renamed"})
        assert again["spec"] == {"a": 1}
        assert again["metadata"]["name"] == "renamed", (
            "an identical republish should still be able to correct a label")
