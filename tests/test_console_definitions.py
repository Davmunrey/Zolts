"""Every number defines itself.

A number without a definition is a number two people read differently in the
same meeting (`docs/28`, OX-10). Every tile and every property label carries
what it counts, over what window, from which table, read from one registry in
the console that this file reads too: a tile added without a sentence fails
here before it reaches a meeting.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKUP = (ROOT / "design" / "console.html").read_text(encoding="utf-8")
SCRIPT = MARKUP[MARKUP.index("<script>"):]


def _registry() -> dict[str, str]:
    start = SCRIPT.index("var DEFINITIONS = ") + len("var DEFINITIONS = ")
    end = SCRIPT.index("\n};", start)
    return json.loads(SCRIPT[start:end + 2])


def _calls(name: str) -> list[str]:
    """The bodies of every `name([...])` call, bracket-matched."""
    out = []
    for m in re.finditer(re.escape(name) + r"\(\[", SCRIPT):
        i, depth = m.end(), 1
        while depth:
            depth += (SCRIPT[i] == "[") - (SCRIPT[i] == "]")
            i += 1
        out.append(SCRIPT[m.start():i])
    return out


def _blocks() -> list[tuple[str, str]]:
    """(label, body) of every `block("Label", ...)` call, paren-matched."""
    out = []
    for m in re.finditer(r'block\("([^"]+)", ', SCRIPT):
        i, depth = m.start() + len("block("), 1
        while depth:
            depth += (SCRIPT[i] == "(") - (SCRIPT[i] == ")")
            i += 1
        out.append((m.group(1), SCRIPT[m.start():i]))
    return out


def _fn(name: str) -> str:
    body = SCRIPT[SCRIPT.index("function " + name + "("):]
    return body[:body.index("\n}")]


REGISTRY = _registry()


def _defined(label: str, group: str | None = None) -> bool:
    """The lookup the console makes, in the same order."""
    keys = ((group or "") + " · " + label, label, label.split(" · ")[0], group or "")
    return any(k in REGISTRY for k in keys)


def test_the_registry_is_one_json_object_and_every_sentence_names_its_source():
    assert len(REGISTRY) >= 100
    for key, text in REGISTRY.items():
        assert text.endswith("."), f"{key!r}: not a sentence"
        assert " from " in text, f"{key!r}: names no source"


def test_every_tile_has_a_definition():
    labels = [label for body in _calls("kpis")
              for label in re.findall(r'label: "([^"]+)"', body)]
    assert len(labels) >= 40, "the tiles were not found"
    missing = sorted({label for label in labels if not _defined(label)})
    assert not missing, f"tiles with no definition: {missing}"


def test_every_property_label_has_a_definition():
    missing = []
    for group, body in _blocks():
        rows = re.findall(r'\[\s*"([^"]+)"\s*,', body)
        if not rows and not _defined(group):
            missing.append(group)
        missing.extend(f"{group} · {row}" for row in rows if not _defined(row, group))
    for label in re.findall(r"'<dt' \+ defAttrs\(\"([^\"]+)\"", SCRIPT):
        if not _defined(label):
            missing.append(label)
    assert not missing, f"property labels with no definition: {sorted(set(missing))}"


def test_a_number_defines_itself_on_hover_and_on_a_tap():
    assert "defAttrs(tile.label)" in _fn("kpis"), "a tile carries no definition"
    assert "defAttrs(kv[0], label, kv[3])" in _fn("block"), "a property carries no definition"
    attrs = _fn("defAttrs")
    assert 'title="' in attrs and "data-def=" in attrs and 'tabindex="0"' in attrs
    assert "function toggleDefinition(" in SCRIPT
    assert 'closest("[data-def]")' in SCRIPT, "nothing opens a definition on a tap"


def test_no_definition_is_orphaned():
    orphans = [key for key in REGISTRY if key.split(" · ")[-1] not in MARKUP]
    assert not orphans, f"definitions nothing renders: {orphans}"
