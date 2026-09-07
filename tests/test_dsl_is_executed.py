"""Every field the DSL defines has something that reads it.

Three of the eleven top-level spec fields were specification with no caller,
and each was invisible until something executed it:

    audience          required by the schema, validated on publish, read by
                      nothing — so a program's exclusions were decoration
    enrich            declared by every shipped program, triggered only by an
                      operator typing a CLI command
    policy.overrides  four keys allowed, one read; a program declaring
                      stricter quiet hours sent at three in the morning

None was found by review. This test is the cheapest form of the check that
would have found them on the first commit: a field the schema accepts and no
module mentions is a promise to a customer that nothing keeps.

It is deliberately coarse, and the coarseness has a measured limit. Mentioning
a field is not executing it, and the mention need not even be the right one:
renaming `overrides.get("lists_check")` leaves this test passing, because
`zolts/overlay.py` names the same key for its own unrelated purpose. That
rename is caught by `tests/test_policy_overrides.py`, which asserts the
override actually applies — verified by making the rename and watching two of
its tests fail.

So the division is: this file catches the twelfth field, added to the schema
by somebody who then forgets the caller; the per-field tests catch a caller
that stops calling; and `scripts/smoke_runtime.py` catches a stage that stops
running.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

SCHEMA = json.loads(Path("examples/schema/zolts-program.schema.json").read_text())
SPEC_FIELDS = sorted(SCHEMA["properties"]["spec"]["properties"])

SOURCES = {path: path.read_text(encoding="utf-8")
           for path in list(Path("runtime").rglob("*.py")) + list(Path("zolts").rglob("*.py"))
           if "test" not in path.name}


def _readers(field: str) -> list[str]:
    """Modules that name this field the way a reader would.

    Quoted or attribute-accessed, not merely containing the word: `route`
    appears in `runtime/agents/spend.py` as an alternative's *kind* and that is
    not a program's routing block.
    """
    pattern = re.compile(rf'''["']{re.escape(field)}["']''')
    return sorted(str(p) for p, text in SOURCES.items() if pattern.search(text))


@pytest.mark.parametrize("field", SPEC_FIELDS)
def test_every_spec_field_has_a_reader(field):
    readers = _readers(field)
    assert readers, (
        f"`spec.{field}` is in the schema and no module under runtime/ or "
        "zolts/ reads it. A field the schema accepts and nothing consumes is a "
        "promise to a customer that nothing keeps — see audience, enrich and "
        "policy.overrides, all of which were exactly this.")


def test_the_field_list_is_read_from_the_schema_not_restated():
    """A list of fields written here would go stale the moment one is added,
    which is the failure this file exists to prevent."""
    assert len(SPEC_FIELDS) >= 10, SPEC_FIELDS
    assert "audience" in SPEC_FIELDS and "enrich" in SPEC_FIELDS


def test_the_detection_would_notice_a_field_nobody_reads():
    """A check that cannot fail is decoration. This proves the rule fires on a
    field no module mentions."""
    assert _readers("a_field_nobody_will_ever_add") == []


@pytest.mark.parametrize("key", sorted(
    SCHEMA["properties"]["spec"]["properties"]["policy"]["properties"]["overrides"]["properties"]))
def test_every_policy_override_has_a_reader(key):
    """The nested case, because this is where the defect actually was: the
    block was read and three of its four keys were dropped."""
    assert _readers(key), (
        f"`policy.overrides.{key}` is in the schema and nothing reads it. An "
        "override that is ignored lets an operator believe they are protected "
        "by a rule nothing applies.")
