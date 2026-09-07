"""The console's own markup, checked the way a browser reads it.

Every defect this file guards against shipped, and none of them failed a test:

  * five views were added against the Programs header, so Spend, Prospects,
    Signals, Policy and Audit all labelled their columns PROGRAM · BLUEPRINT ·
    ENROLLED · LIFT · HOLDOUT · P95;
  * the grid was sized for Programs, so every other view's values wrapped and
    its rows grew into each other;
  * `.st.warn` and `.st.deny` were never written, so every amber and red status
    dot rendered as nothing — including the mailbox in `complaint.alarm`, the
    one row the Sending view exists to show;
  * `.seg` was never written, so six jurisdiction buttons rendered as one run
    of letters, CADEESFRGBUS, in the middle of a heading.

They have one shape in common: a name used in one place and defined in
another, with nothing holding the two together.
"""

from __future__ import annotations

import re
from pathlib import Path

CONSOLE = Path(__file__).resolve().parent.parent / "design" / "console.html"
MARKUP = CONSOLE.read_text(encoding="utf-8")
STYLE = MARKUP[MARKUP.index("<style>"):MARKUP.index("</style>")]
SCRIPT = MARKUP[MARKUP.index("<script>"):]


def _views() -> dict[str, dict[str, object]]:
    """The view registry, read out of the script rather than restated here."""
    block = SCRIPT[SCRIPT.index("var VIEWS = {"):]
    block = block[:block.index("\n};")]
    found: dict[str, dict[str, object]] = {}
    for name, body in re.findall(r"\n  (\w+): \{(.*?)\n  \}", block, re.S):
        cols = re.search(r'cols: "([^"]*)"', body)
        head = re.search(r"head: \[(.*?)\]", body, re.S)
        found[name] = {
            "cols": cols.group(1) if cols else "",
            "head": re.findall(r'"([^"]*)"', head.group(1)) if head else [],
        }
    return found


def _columns(spec: str) -> int:
    """Count the tracks in a grid-template-columns value.

    `minmax(140px,1.35fr)` is one track and contains a comma, so splitting on
    whitespace outside brackets is the only reading that agrees with CSS.
    """
    depth, tracks, current = 0, 0, ""
    for ch in spec:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch.isspace() and depth == 0:
            if current:
                tracks += 1
            current = ""
        else:
            current += ch
    return tracks + (1 if current else 0)


def test_every_view_is_registered():
    """A view the rail can reach and the registry does not know falls back to
    the Programs columns, which is how this went wrong the first time."""
    rail = set(re.findall(r'data-view="(\w+)"', MARKUP))
    assert rail, "the rail has no entries at all"
    assert rail <= set(_views()), f"unregistered: {sorted(rail - set(_views()))}"


def test_each_view_labels_exactly_the_columns_it_has():
    for name, spec in _views().items():
        tracks = _columns(str(spec["cols"]))
        labels = len(spec["head"])
        assert tracks == labels, (
            f"{name}: {tracks} columns and {labels} header labels. A header "
            f"shorter than its grid leaves a column unnamed; a longer one "
            f"pushes every label one cell to the left")


# -- a class used is a class defined --------------------------------------

# Emitted from data at run time rather than written as a literal, so the
# scanner cannot see them and each is checked by the test that owns it.
DYNAMIC = {"live", "paused", "draft", "warn", "deny", "up", "flat", "down", "none"}


def _defined(prefix: str) -> set[str]:
    return set(re.findall(rf"\.{prefix}\.([a-z-]+)", STYLE))


def test_every_status_dot_has_a_colour():
    """`.st.warn` and `.st.deny` were used from the day the Sending, Review,
    Policy and Prospects views shipped and neither was ever written, so the
    dot that says a mailbox is in `complaint.alarm` was invisible."""
    used = set(re.findall(r'dot\("(\w+)"\)', SCRIPT))
    used |= set(re.findall(r'"st (\w+)"', SCRIPT))
    used |= set(re.findall(r"dot\((?:[^)]*?)\? \"(\w+)\" : \"(\w+)\"", SCRIPT)[0]
                if re.findall(r"dot\((?:[^)]*?)\? \"(\w+)\" : \"(\w+)\"", SCRIPT) else [])
    used.discard("")
    missing = sorted(used - _defined("st"))
    assert not missing, f"status dots with no colour rule: {missing}"


def _calls(name: str) -> list[str]:
    """The argument text of every `name(...)` call, parentheses balanced.

    A regex reading to the next quote walked out of one call and into the
    next: `rowOf(..., "sub")` looked like a `cell()` modifier. Counting
    brackets is the only reading that stops where the call does.
    """
    out: list[str] = []
    for match in re.finditer(name + r"\(", SCRIPT):
        depth, i = 0, match.end() - 1
        while i < len(SCRIPT):
            if SCRIPT[i] == "(":
                depth += 1
            elif SCRIPT[i] == ")":
                depth -= 1
                if depth == 0:
                    out.append(SCRIPT[match.end():i])
                    break
            i += 1
    return out


def test_every_cell_modifier_has_a_rule():
    """A modifier with no rule is a value the reader cannot tell apart from
    every other value — a denial that does not look denied."""
    used: set[str] = set()
    for args in _calls("cell"):
        # The modifier is the last string literal in the call; everything
        # before it is the value being displayed.
        literals = re.findall(r'"([a-z]+(?: [a-z]+)*)"\s*$', args.strip())
        for group in literals:
            used |= {c for c in group.split() if c}
    missing = sorted(used - _defined("cell") - {"cell"})
    assert not missing, f"cell modifiers with no rule: {missing}"


def test_every_component_class_in_the_markup_is_styled():
    """`.seg` had no rule at all, so a segmented control rendered as one run of
    letters inside a heading. The class existed; the style did not."""
    used = set()
    for group in re.findall(r'class="([a-zA-Z0-9 _-]+)"', MARKUP):
        used |= {c for c in group.split() if c and not c.startswith("only-")}
    # The class has to end a compound selector, or the rule styles something
    # inside it rather than it: `.seg button{}` leaves `.seg` itself as bare
    # as it was when six buttons ran together into CADEESFRGBUS.
    styled = {c for c in used
              if re.search(r"\." + re.escape(c) + r"\s*(?=[{,:.\[])", STYLE)}
    missing = sorted(used - styled - DYNAMIC)
    assert not missing, f"classes used in the markup with no rule of their own: {missing}"
