"""Live, no reload: the console patches what changed and never navigates.

Every action used to end in `window.location.reload()`: the operator approved
a draft and was dumped back through a full page load, their scroll and their
selection gone, and nothing on the screen moved until somebody pressed F5
(`docs/28`, OX-6). The console now re-reads `/v1/console` after every action
and every thirty seconds while the tab is visible, and re-renders in place.

Three properties, each guarded: no full-page reload remains in the surface;
an unchanged view model answers 304 to the ETag it was served with, so a
quiet console costs one small request; and a re-render never lands on a
field the operator is typing in.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.conftest import requires_db

ROOT = Path(__file__).resolve().parent.parent
MARKUP = (ROOT / "design" / "console.html").read_text(encoding="utf-8")
SCRIPT = MARKUP[MARKUP.index("<script>"):]


def _fn(name: str) -> str:
    body = SCRIPT[SCRIPT.index("function " + name + "("):]
    return body[:body.index("\n}")]


def test_no_full_page_reload_remains_in_the_surface():
    assert "location.reload" not in SCRIPT, "an action still throws the page away"
    assert "location.href" not in SCRIPT and "location.assign" not in SCRIPT


def test_every_action_refreshes_in_place():
    """Seven actions used to reload; each now asks for fresh data."""
    starts = [m.end() for m in re.finditer(r"if \(res\.ok\)\{", SCRIPT)]
    assert len(starts) >= 6, "the action handlers moved"
    # Each success branch reaches a refresh within its own few lines; a
    # brace-balanced parse is more than a guard needs.
    acted = [SCRIPT[start:start + 700] for start in starts]
    assert all("refresh(" in handler for handler in acted), (
        [h[:120] for h in acted if "refresh(" not in h])
    # Activation reads the server's lint before it refreshes, so its handler
    # has a different shape; it still ends in a refresh rather than a reload.
    activation = SCRIPT[SCRIPT.index('sessionStorage.setItem("zolts.activation-note"'):]
    assert "refresh();" in activation[:400]


def test_the_refresh_sends_the_etag_and_skips_a_304():
    body = _fn("refresh")
    assert "If-None-Match" in body and "304" in body
    assert "render()" in body or "applyFresh(" in body
    assert "renderChrome()" in _fn("applyFresh")


def test_polling_pauses_while_the_tab_is_hidden_and_resumes_at_once():
    assert "visibilitychange" in SCRIPT
    assert "setInterval(" in SCRIPT and "POLL_MS" in SCRIPT
    assert re.search(r"var POLL_MS = (\d+)", SCRIPT), "the interval is not named"
    assert int(re.search(r"var POLL_MS = (\d+)", SCRIPT).group(1)) >= 10_000, (
        "a poll under ten seconds is a load, not a refresh")


def test_a_re_render_never_lands_on_a_field_the_operator_is_typing_in():
    body = _fn("applyFresh")
    assert "if (busy()) return false;" in body, (
        "fresh data would replace the editor and the reason field under the cursor")
    guard = _fn("busy")
    assert "typing()" in guard and "state.tuning" in guard and "inflight" in guard
    assert "activeElement" in _fn("typing"), "typing() does not look at the cursor"
    # The tag advances only when the body is rendered, so a dropped refresh
    # is asked for again on the next poll rather than lost behind a 304.
    assert body.index("if (busy()) return false;") < body.index("etag = tag || etag;")
    assert "inflight += 1" in _fn("act") and "inflight -= 1" in _fn("act")
    # A refresh that started before the latest action is dropped when it
    # lands after it: that action's own refresh is the one that should land.
    assert "epoch += 1" in _fn("act")
    assert "started !== epoch" in body and "var started = epoch;" in _fn("refresh")


@requires_db
def test_the_console_endpoint_answers_304_to_the_etag_it_served(db, tenant):
    from fastapi.testclient import TestClient

    from runtime.api.app import create_app
    from runtime.provision import issue_api_key

    client = TestClient(create_app(db), raise_server_exceptions=False)
    token = issue_api_key(db, str(tenant["id"]), "test", []).token
    first = client.get("/v1/console", headers={"x-api-key": token})
    assert first.status_code == 200 and first.headers.get("etag")
    again = client.get("/v1/console", headers={"x-api-key": token,
                                               "if-none-match": first.headers["etag"]})
    assert again.status_code == 304 and not again.content
    stale = client.get("/v1/console", headers={"x-api-key": token,
                                               "if-none-match": '"not-this-one"'})
    assert stale.status_code == 200
