"""Keyboard and screen reader.

Accessibility is table stakes for enterprise procurement and it is cheaper now
than after the surface grows (`docs/28`, OX-13). `DESIGN.md` wrote the list
contract — one tab stop, options under it, the pointer named by
`aria-activedescendant` — and the console had a tab stop per row and moved
the pointer only on Programs. The automated pass lives in the browser check;
this file holds the shapes the pass relies on, and the two defects it found.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKUP = (ROOT / "design" / "console.html").read_text(encoding="utf-8")
STYLE = MARKUP[MARKUP.index("<style>"):MARKUP.index("</style>")]
SCRIPT = MARKUP[MARKUP.index("<script>"):]
CHECK = (ROOT / "scripts" / "browser_console.py").read_text(encoding="utf-8")


def _token(name: str) -> str:
    found = re.search(r"--%s:\s*(#[0-9a-fA-F]{6})" % re.escape(name), STYLE)
    assert found, f"no token --{name}"
    return found.group(1)


def _luminance(hex_colour: str) -> float:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def test_the_list_is_one_tab_stop_and_its_rows_are_options():
    assert 'role="row"' not in SCRIPT, "a row is still announced as a table row"
    assert SCRIPT.count('role="option"') >= 8, "rows are not options of the list"
    assert 'tabindex="0" role="option"' not in SCRIPT and 'role="option" tabindex="0"' not in SCRIPT, (
        "a row is its own tab stop; DESIGN.md wants one per list")
    assert 'list.setAttribute("aria-activedescendant", id)' in SCRIPT
    assert "new MutationObserver(pointRows).observe(list" in SCRIPT, (
        "rows are pointed by some renderers and not others")


def test_arrow_keys_move_the_pointer_on_every_view_not_only_programs():
    """ArrowDown on the review queue painted the programme list over it (D-109):
    the handler called the programme mover whatever the view."""
    handler = SCRIPT[SCRIPT.index('if (e.key === "j" || e.key === "ArrowDown")'):]
    handler = handler[:handler.index("});")]
    assert "stepRow(1)" in handler and "stepRow(-1)" in handler
    assert "move(1)" not in handler, "the programme mover is still called on every view"
    step = SCRIPT[SCRIPT.index("function stepRow("):]
    step = step[:step.index("\n}")]
    assert 'state.view === "programs"' in step and "move(step)" in step


def test_every_icon_is_hidden_from_the_accessibility_tree():
    assert MARKUP.count("<svg ") == MARKUP.count('<svg aria-hidden="true"'), (
        "an icon with no name is read out as nothing, or as its path")


def test_the_tertiary_ink_meets_the_contrast_floor_on_every_surface():
    """Measured, not assumed: 4.5 is the AA floor for the 11px labels the
    tertiary ink is used for, and the old value sat at 4.48 on the canvas
    and 3.66 on the darkest surface (D-108)."""
    ink = _token("ink-tertiary")
    for surface in ("canvas", "surface-1", "surface-2", "surface-3"):
        assert contrast(ink, _token(surface)) >= 4.5, (surface, contrast(ink, _token(surface)))


def test_refusals_are_announced():
    for element_id in ("pubnote", "batch-msg", "sc-msg", "buy-msg", "ob-msg"):
        assert re.search(r'id="%s" role="status"' % element_id, SCRIPT), (
            f"#{element_id} changes and a screen reader is not told")
    assert '<p class="empty" role="status">' in SCRIPT


def test_the_console_has_a_main_landmark_and_the_list_names_its_view():
    assert '<main class="centre">' in MARKUP
    assert 'box.setAttribute("aria-label", spec.title)' in SCRIPT
    assert ':focus-visible{outline:2px solid var(--accent-ring)' in STYLE
    assert 'id="open-pal" aria-label="Command palette"' in MARKUP


def test_the_check_runs_the_pass_on_the_door_and_on_every_view():
    assert 'report["door_a11y"] = page.evaluate(A11Y_PASS)' in CHECK
    loop = CHECK[CHECK.index('for entry in page.locator("nav a[data-view]").all():'):]
    loop = loop[:loop.index('report["cut_off_on_a_wide_screen"]')]
    assert "found = page.evaluate(A11Y_PASS)" in loop
    for rule in ("'name'", "'reference'", "'listbox-child'", "'image'", "'contrast'"):
        assert rule in CHECK, f"the pass has no {rule} rule"
