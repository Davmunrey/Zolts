"""The phone approves.

Below the phone breakpoint the panel sits under the list, so a row tapped at
the top of the screen changed something below the fold and the tap looked
like nothing (`docs/28`, OX-9). The panel now comes to the thumb, the buttons
that decide are sized for one, and the breakpoint is one number the
stylesheet and the script share. That a phone approves is proved by the
browser check, which reads the draft and the task back from the database.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKUP = (ROOT / "design" / "console.html").read_text(encoding="utf-8")
STYLE = MARKUP[MARKUP.index("<style>"):MARKUP.index("</style>")]
SCRIPT = MARKUP[MARKUP.index("<script>"):]
PHONE = re.search(r'var PHONE = matchMedia\("\(max-width:(\d+)px\)"\)', SCRIPT)


def _media_block(width: int) -> str:
    """The `@media (max-width:<width>px)` block, closed by brace count."""
    start = STYLE.index(f"@media (max-width:{width}px){{")
    depth = 0
    for i in range(start, len(STYLE)):
        if STYLE[i] == "{":
            depth += 1
        elif STYLE[i] == "}":
            depth -= 1
            if depth == 0:
                return STYLE[start:i + 1]
    raise AssertionError("the media block never closes")


def _fn(name: str) -> str:
    body = SCRIPT[SCRIPT.index("function " + name + "("):]
    return body[:body.index("\n}")]


def test_the_breakpoint_is_one_number_the_stylesheet_and_the_script_share():
    assert PHONE, "the script does not declare the phone breakpoint"
    block = _media_block(int(PHONE.group(1)))
    assert "nav{flex-direction:row" in block, (
        "the script's breakpoint is not the one that turns the rail into a strip")


def test_the_panel_comes_to_the_thumb_after_a_plain_tap():
    fn = _fn("bringPanelToTheThumb")
    assert "PHONE.matches" in fn, "the panel would come to a pointer too"
    assert "modified(e)" in fn, "a batch tap would leave the rows being chosen"
    assert "detail.scrollIntoView(" in fn, "the panel stays below the fold"
    assert 'list.addEventListener("click", bringPanelToTheThumb)' in SCRIPT


def test_the_buttons_that_decide_are_sized_for_a_thumb():
    block = _media_block(int(PHONE.group(1)))
    rule = re.search(r"\n  ([^\n{]*\.dactions \.btn[^\n{]*)\{([^}]*)\}", block)
    assert rule, "no phone rule sizes the deciding buttons"
    selectors, body = rule.group(1), rule.group(2)
    for selector in (".actf .btn", ".batch .btn"):
        assert selector in selectors, f"{selector} is not sized for a thumb"
    height = re.search(r"height:(\d+)px", body)
    assert height and int(height.group(1)) >= 40, body
