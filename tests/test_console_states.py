"""States that say something.

An empty screen that says nothing looks like a screen that failed (`docs/28`,
OX-12). Every empty, loading and error state names what the runtime is doing
or what is missing. The blank assignments are refused here; that every view
rendered with an emptied model carries a sentence on both sides is the browser
check's job, which renders them.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKUP = (ROOT / "design" / "console.html").read_text(encoding="utf-8")
SCRIPT = MARKUP[MARKUP.index("<script>"):]
CHECK = (ROOT / "scripts" / "browser_console.py").read_text(encoding="utf-8")


def test_no_renderer_leaves_the_list_or_the_panel_blank():
    blank = re.findall(r"(?:list|detail)\.innerHTML = (?:\"\"|\'\');", SCRIPT)
    assert not blank, f"{len(blank)} renderer(s) assign an empty string to a screen side"


def test_every_empty_state_is_a_sentence_that_starts_with_what_is_missing():
    texts = re.findall(r"empty\((?:[^\"()]*)\"([^\"]*)\"", SCRIPT)
    # Every call site is read, so a call whose first words are not a string
    # literal cannot slip past as "not found".
    assert len(texts) == SCRIPT.count("empty(") - SCRIPT.count("function empty("), (
        "an empty state is built from something this test cannot read")
    assert len(texts) >= 12, "the empty states were not found"
    for text in texts:
        assert text, "an empty state with no words in it"
        assert text[0].isupper(), f"not a sentence: {text!r}"
        assert "." in text, f"no full stop, so no sentence: {text!r}"
        assert len(text.split()) >= 4, f"a label, not a sentence: {text!r}"


def test_every_loading_and_error_state_is_named_and_says_something():
    for kind in ("pv", "tl"):
        loading = re.search(r'id="%s-loading">([^<]+)<' % kind, SCRIPT)
        assert loading and len(loading.group(1).split()) >= 3, kind
        assert loading.group(1).endswith("…"), "a loading state says it is still going"
        assert 'id="%s-error"><b>' % kind in SCRIPT, f"the {kind} error state does not name the error"


def test_the_check_opens_a_tenant_with_nothing_and_walks_every_view():
    """The exit criterion of OX-12 lives in the browser check: a tenant with
    nothing opens the console and every rail entry is rendered and read. This
    file holds the check to every entry rather than a chosen few, and to a
    blueprint that ships no starter programme, or the tenant is not empty."""
    assert "def _empty_tenant(" in CHECK
    assert "signed_up[\"programs\"]" in CHECK, "the check does not establish that the tenant is empty"
    walk = CHECK[CHECK.index("blank.fill(\"#k\", _empty_tenant())"):]
    walk = walk[:walk.index("blank.close()")]
    assert 'blank.locator("nav a[data-view]").all()' in walk
    assert "blank.evaluate(SAYS_SOMETHING)" in walk
    assert "rows > 0 || sentence.test(list)" in CHECK, "an empty list may stay silent"
    assert "listSays && sentence.test(panel)" in CHECK, "a panel may stay silent"
