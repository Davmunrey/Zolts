"""Vercel's entry point.

Vercel looks for a variable named `app` in this file and serves it as one
function; `vercel.json` rewrites every path the static site does not answer to
it, and its cron calls `/api/tick` once a minute. Everything is in
`runtime.serverless` — this file exists because the platform names the path
(ADR-041).
"""

from __future__ import annotations

import sys
from pathlib import Path

# The repository root, so `runtime` and `zolts` import the same way they do
# from the CLI and the tests, whatever directory the function is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runtime.serverless import application  # noqa: E402 - after the path fix

app = application()
