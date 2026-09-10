"""What production is serving, against what this checkout would serve.

Thirty-nine consecutive runs of `deploy-vercel` reported success while
production served a build from weeks earlier. Nothing was broken and nothing
lied: the release job skips rather than fails when the Vercel secrets are
absent, deliberately, so that red keeps meaning something. The cost is that
green stopped meaning anything — **no check anywhere compared the deployed
surface with the one on `main`** (D-100).

The founder found it by opening the page and recognising an old screen. That is
the failure mode this repository catalogues sixty times over: a complete
specification with no caller, here applied to the deployment itself.

A missing credential is a *setup* state and skipping on it is right. A stale
production is a *product* state, and it stays red until somebody deploys. The
two are different, and this script only reports the second.

It needs no secret. It opens the public address and reads the build stamp
`runtime.surface.document` puts in every page it assembles — the digest of the
surface template, taken before any tenant's data is injected, so the static
build and the API serving live figures stamp the same twelve characters for the
same code.

    python3 scripts/deploy_drift.py --url https://zolts.vercel.app

Exit codes, because the workflow reads them and a person reads the message:

    0   the deployed stamp is this checkout's
    1   drift: the page carries a different stamp, or none at all
    2   no verdict: the address could not be opened

Two is not a pass. A check that cannot reach its subject and returns zero is a
guard that survives the thing it guards against, which is the shape
`scripts/ambient_check.py` exists to catch.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime.surface import build_id  # noqa: E402 - after the path fix

SURFACE = ROOT / "design" / "console.html"
DEFAULT_URL = "https://zolts.vercel.app"
STAMP = re.compile(r'<meta\s+name="zolts-build"\s+content="([0-9a-f]{12})"')

# The whole page is not needed and some of it is a megabyte of inlined fixture.
# The stamp is in the head, which is the first few kilobytes.
HEAD_BYTES = 8192


def deployed_stamp(url: str, timeout: float = 20.0) -> str | None:
    """The stamp the address serves, or None when it serves a page without one.

    A page with no stamp is drift, not an error: every build since this script
    existed carries one, so its absence means the deployment predates it — the
    exact condition that went unnoticed.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "zolts-drift/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        head = response.read(HEAD_BYTES).decode("utf-8", errors="replace")
    found = STAMP.search(head)
    return found.group(1) if found else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL,
                        help=f"the production address (default {DEFAULT_URL})")
    args = parser.parse_args()

    if not SURFACE.is_file():
        print(f"::error::the surface is missing from this checkout ({SURFACE}), "
              f"so there is nothing to compare against", file=sys.stderr)
        return 2

    expected = build_id(SURFACE.read_text(encoding="utf-8"))

    try:
        serving = deployed_stamp(args.url)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        print(f"::error::{args.url} could not be opened ({exc}), so whether "
              f"production is current is unknown. This is not a pass.",
              file=sys.stderr)
        return 2

    if serving == expected:
        print(f"{args.url} is serving {expected}, which is this checkout's build")
        return 0

    if serving is None:
        print(f"::error::{args.url} serves a page with no build stamp, so it "
              f"predates the stamp itself. This checkout builds {expected}. "
              f"Production has not been released from this pipeline: "
              f"`vercel.json` turns the platform's own deploys of `main` off, "
              f"and the release job skips while VERCEL_TOKEN, VERCEL_ORG_ID "
              f"and VERCEL_PROJECT_ID are unset.", file=sys.stderr)
        return 1

    print(f"::error::{args.url} is serving build {serving} and this checkout "
          f"builds {expected}. Production is behind `main`.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
