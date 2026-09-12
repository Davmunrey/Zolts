"""A palette that reaches everything, and no dead commands.

`New program from blueprint` ran `function(){}`: a command that did nothing,
which is D-38 inside the palette (`docs/28`, OX-8). Every screen, every
programme, every contact and every command is now reachable from ⌘K, and
a command whose handler has an empty body fails this file.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKUP = (ROOT / "design" / "console.html").read_text(encoding="utf-8")
SCRIPT = MARKUP[MARKUP.index("<script>"):]


def _fn(name: str) -> str:
    body = SCRIPT[SCRIPT.index("function " + name + "("):]
    return body[:body.index("\n}")]


def test_no_palette_command_has_an_empty_handler():
    empty = re.findall(r"run:\s*function\(\)\s*\{\s*\}", SCRIPT)
    assert not empty, f"{len(empty)} palette command(s) do nothing"
    assert "New program from blueprint" not in SCRIPT, (
        "the command with no flow behind it is back")


def test_every_screen_is_reachable_from_the_palette():
    screens = _fn("palScreens")
    assert "Object.keys(VIEWS)" in screens and "show(view)" in screens, (
        "the palette lists screens by hand instead of from the registry")


def test_a_contact_is_reachable_and_opens_on_their_timeline():
    contacts = _fn("palContacts")
    assert "D.prospects" in contacts and "loadTimeline(c.id)" in contacts
    assert 'show("prospects")' in contacts


def test_the_palette_groups_every_kind_it_reaches():
    render = _fn("palRender")
    for title in ("Screens", "Programs", "Contacts", "Commands"):
        assert f'"{title}"' in render or f"'{title}'" in render or f">{title}<" in render, (
            f"the palette no longer groups {title}")
