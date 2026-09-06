"""The page a browser gets when it has no session.

`/console` answered 401 to anyone who typed its URL, because it authenticated
by a header a browser cannot send on navigation. The quickstart's own
instruction was to `curl` it — which renders the page and can click nothing on
it. This is the door.

It asks for the API key once, exchanges it for a session, and never stores the
key: the key goes in one request body and the browser keeps a short-lived
session cookie instead.
"""

from __future__ import annotations

import base64
import hashlib

SCRIPT = """
const form = document.getElementById('f');
const err = document.getElementById('e');
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  err.textContent = '';
  const key = document.getElementById('k').value.trim();
  if (!key) { err.textContent = 'Paste the API key you were shown at signup.'; return; }
  const response = await fetch('/v1/console/session', {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({api_key: key}),
  });
  if (response.ok) {
    // A fresh navigation, not a reload. This page was served with 401, and
    // reloading an error response is not a reliable way to reveal the
    // authenticated one; `replace` also keeps the sign-in page out of history,
    // so Back does not land on a door already opened.
    window.location.replace('/console');
    return;
  }
  err.textContent = response.status === 401
    ? 'That key is not valid, or it has been revoked.'
    : 'Could not sign in (' + response.status + ').';
});
"""

_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zolts — sign in</title>
<style>
  :root {{ color-scheme: light dark; --ink: #14161a; --dim: #5b6270; --line: #d9dde5;
           --bg: #f7f8fa; --card: #fff; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --ink: #e9ecf2; --dim: #97a0b0; --line: #2b3038; --bg: #101216; --card: #171a20; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: var(--bg);
         color: var(--ink); font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, sans-serif; }}
  .card {{ width: min(420px, calc(100vw - 32px)); background: var(--card); padding: 32px;
           border: 1px solid var(--line); border-radius: 12px; }}
  h1 {{ margin: 0 0 4px; font-size: 18px; letter-spacing: -0.01em; }}
  p {{ margin: 0 0 20px; color: var(--dim); }}
  label {{ display: block; font-weight: 600; margin-bottom: 6px; }}
  input {{ width: 100%; padding: 9px 11px; font: inherit; font-family: ui-monospace, monospace;
           color: var(--ink); background: transparent;
           border: 1px solid var(--line); border-radius: 7px; }}
  button {{ width: 100%; margin-top: 14px; padding: 10px; font: inherit; font-weight: 600;
            color: #fff; background: #14161a; border: 0; border-radius: 7px; cursor: pointer; }}
  @media (prefers-color-scheme: dark) {{ button {{ color: #14161a; background: #e9ecf2; }} }}
  .err {{ margin-top: 12px; color: #b4232a; min-height: 1.2em; }}
  .note {{ margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--line);
           color: var(--dim); font-size: 13px; }}
  code {{ font-family: ui-monospace, monospace; }}
</style>
</head><body>
<main class="card">
  <h1>Zolts</h1>
  <p>Sign in with the API key you were shown at signup.</p>
  <form id="f">
    <label for="k">API key</label>
    <input id="k" name="k" type="password" autocomplete="off" spellcheck="false"
           placeholder="zk_…" autofocus>
    <button type="submit">Open the console</button>
  </form>
  <div class="err" id="e" role="alert"></div>
  <p class="note">The key is exchanged for a session that lasts 12 hours and is
  stored nowhere in this browser. Revoking the key ends the session.</p>
</main>
<script>{script}</script>
</body></html>
"""


def sign_in_page() -> str:
    return _PAGE.format(script=SCRIPT)


def _sha256_source(script: str) -> str:
    digest = hashlib.sha256(script.encode("utf-8")).digest()
    return f"'sha256-{base64.b64encode(digest).decode()}'"


# Derived from the bytes actually served, the same way the static build derives
# the console's policy. A hash written by hand is a hash that drifts.
SIGN_IN_CSP = (
    "default-src 'none'; "
    f"script-src {_sha256_source(SCRIPT)}; "
    "style-src 'unsafe-inline'; "
    "connect-src 'self'; "
    "form-action 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)
