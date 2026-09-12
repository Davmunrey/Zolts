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


def test_a_selected_row_is_visible():
    """Five views mark the row whose story the panel shows with `on`, and no
    rule ever styled it: the operator's selection was invisible on every one
    of them (D-106). `DESIGN.md` names the surface a selected row uses, and
    the class is emitted from data, so the scanner above cannot see it."""
    emitters = re.findall(r'\? " on" : ""', SCRIPT)
    assert len(emitters) >= 5, "the selected-row marker is no longer emitted"
    assert re.search(r"\.row\.on\s*\{[^}]*var\(--accent-dim\)", STYLE), (
        "the selected row has no rule on the accent's dim, so the selection is invisible")


# -- the stat tiles -------------------------------------------------------

def test_the_stat_tiles_are_cleared_before_a_view_renders():
    """Otherwise a view that sets none shows the previous view's numbers.

    That is worse than showing nothing: the tiles are the first thing read and
    they would be confidently wrong. The reset is one line in `render()`, and
    it has to run before the dispatch rather than inside each renderer — which
    is exactly how five views came to share the Programs header.
    """
    body = SCRIPT[SCRIPT.index("function render(){"):]
    body = body[:body.index("\n}")]
    reset = body.index("kpis([])")
    first_view = min(body.index('state.view === "' + v + '"')
                     for v in ("prospects", "signals", "spend", "policy", "audit"))
    assert reset < first_view, "the tiles are cleared after a view has already rendered"


def test_a_meter_cannot_overflow_its_track():
    """A share above 1 draws a fill wider than the bar it is in, which reads as
    a different number than the one beside it."""
    builder = SCRIPT[SCRIPT.index("function kpis(tiles)"):]
    builder = builder[:builder.index("\n}")]
    assert "Math.max(0, Math.min(1," in builder, (
        "the meter's share reaches the width without being clamped")


# -- prose columns --------------------------------------------------------

def test_a_prose_column_declares_itself_and_has_a_rule():
    """A rationale is a sentence, and no column width fits one on a line.

    `whatsapp requires consent, contact has none on record` was 245px wider
    than its column at 1280px — widening the column could never have fixed it.
    The column that carries prose says so with `w:` in the same head spec that
    names it, and wraps. If the marker existed without a rule, the cell would
    silently keep clipping while the spec claimed otherwise.
    """
    prose = {name: [h for h in spec["head"] if h.startswith("w:")]
             for name, spec in _views().items()}
    declared = {name: cols for name, cols in prose.items() if cols}
    assert declared, "no view declares a prose column; policy carries sentences"
    assert re.search(r"\.cell\.w\s*\{", STYLE), (
        "views declare prose columns but `.cell.w` has no rule, so they clip")
    assert "white-space:normal" in STYLE[STYLE.index(".cell.w{"):
                                        STYLE.index(".cell.w{") + 220], (
        "`.cell.w` does not release nowrap, so a declared prose column clips")


def test_the_prefix_never_reaches_the_screen():
    """`r:` and `w:` are instructions to the renderer, not text. A header that
    printed `w:Rationale`, or a phone row labelled `w:Reason`, would be the
    marker leaking into the product."""
    assert re.search(r"function label\(h\)\s*\{[^}]*replace\(/\^\[rw\]:/", SCRIPT), (
        "no single place strips the head prefixes; two readers will diverge")
    chrome = SCRIPT[SCRIPT.index("function chrome(view)"):]
    chrome = chrome[:chrome.index("\n}")]
    assert "label(h)" in chrome, "the header renders the raw head entry"
    # A section that carries its own head renders it through the same
    # function, or the Signals list would print `r:Fired` above the funnel.
    sect = SCRIPT[SCRIPT.index("function sect(title, note, own)"):]
    sect = sect[:sect.index("\n}")]
    assert "label(h)" in sect, "a section head renders the raw head entry"
    labelled = SCRIPT[SCRIPT.index("function labelled(cells"):]
    labelled = labelled[:labelled.index("\n}")]
    assert "label(head[i])" in labelled, "the phone label renders the raw entry"


def test_no_column_holding_variable_length_text_is_a_fixed_width():
    """A 1920px screen has ~500px of width the console used to give to gutter
    while `local-services-multisite` ellipsised in a hard-coded 152px column.
    Fixed widths belong to columns whose content has a known size — a dot, a
    percentage, a count — not to names, keys or identifiers."""
    for name, spec in _views().items():
        tracks = spec["cols"].split()
        for i, track in enumerate(tracks):
            label = re.sub(r"^[rw]:", "", spec["head"][i]) if i < len(spec["head"]) else ""
            if label in ("", "Enrolled", "Lift", "Holdout", "p95", "Score", "Capacity",
                         "Credits", "Events", "Cost", "Strength", "Detected in",
                         "When", "Observed", "Health", "State", "Channel"):
                continue
            assert not re.fullmatch(r"[\d.]+px", track), (
                f"{name}: the {label!r} column is fixed at {track}; on a wide "
                "screen the spare width goes to gutter and the text still clips")
