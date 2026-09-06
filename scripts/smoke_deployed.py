"""Check a deployed Zolts over HTTP, the way the internet reaches it.

`smoke_runtime.py` proves the loop against a database. Fly's release command
proves the runtime may take traffic. Neither of them opens the URL.

That gap has already cost this repository once: the console was built, served,
tested against its view model and rendered headlessly, and still answered 401
to anyone who typed its address, because it authenticated by a header a
browser cannot send on navigation. Every check was green. Nobody had opened
it.

So this asserts only what a stranger with the URL can observe, and it needs no
credentials to do it:

    python3 scripts/smoke_deployed.py --url https://zolts.fly.dev

An API key is optional. With one it also reads a tenant-scoped endpoint, which
is the difference between "the process is up" and "the product answers".
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

TIMEOUT = 15


def _get(url: str, key: str | None = None) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, headers={"user-agent": "zolts-smoke"})
    if key:
        request.add_header("x-api-key", key)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="base URL of the deployment")
    parser.add_argument("--key", help="an API key, to check a tenant-scoped read")
    parser.add_argument("--allow-insecure", action="store_true",
                        help="permit http:// — for a local rehearsal only")
    parser.add_argument("--strict", action="store_true",
                        help="fail on any liveness signal, not only on a broken "
                             "deployment. For a run after the tenant is configured")
    args = parser.parse_args()

    base = args.url.rstrip("/")
    report: dict[str, object] = {"url": base}
    failures: list[str] = []
    notes: list[str] = []

    # TLS is not a nicety on a surface that carries an API key in a header.
    if base.startswith("http://") and not args.allow_insecure:
        print("::error::refusing to smoke a plaintext URL; pass --allow-insecure "
              "only for a local rehearsal", file=sys.stderr)
        return 2

    status, headers, body = _get(f"{base}/health")
    report["health"] = status
    if status != 200:
        failures.append(f"/health answered {status}")
    else:
        report["health_body"] = json.loads(body or b"{}")

    # The endpoint that distinguishes "up" from "doing its job". Its signals
    # are reported and not fatal by default: a freshly deployed instance
    # legitimately has a tenant with no connector yet, and failing the smoke
    # for that would train whoever runs it to ignore the output. `--strict`
    # is for the run that happens after the deployment is configured.
    status, _, body = _get(f"{base}/health/liveness")
    report["liveness"] = status
    if status not in (200, 503):
        failures.append(f"/health/liveness answered {status}")
    else:
        live = json.loads(body or b"{}")
        report["draining"] = live.get("draining")
        report["signals"] = {s["name"]: s["ok"] for s in live.get("signals", [])}
        attention = [n for n, ok in report["signals"].items() if not ok]
        report["needsAttention"] = attention
        for name in attention:
            (failures if args.strict else notes).append(
                f"liveness signal '{name}' is not ok")

    # The one a person types. What matters is that a body comes back that a
    # person can act on — the sign-in door, or the console itself.
    #
    # The status is deliberately 401 on the door: the request really is
    # unauthenticated, and a browser renders the body of a 401 perfectly well.
    # Asserting 200 here was this script's own first bug, and it is the same
    # mistake in reverse as the one that made the console unopenable: reading
    # the status instead of asking what the person sees.
    status, headers, body = _get(f"{base}/console")
    report["console"] = status
    text = body.decode("utf-8", "replace")
    if status not in (200, 401):
        failures.append(f"/console answered {status}")
    elif "<form" not in text and "id=\"f\"" not in text and "class=\"app\"" not in text:
        failures.append("/console served neither a sign-in form nor the console")

    csp = headers.get("Content-Security-Policy") or headers.get("content-security-policy")
    report["csp"] = bool(csp)
    if not csp:
        failures.append("/console served no Content-Security-Policy")

    if args.key:
        status, _, body = _get(f"{base}/v1/billing/current", key=args.key)
        report["billing"] = status
        if status != 200:
            failures.append(f"/v1/billing/current answered {status} to a valid key")
        else:
            report["plan"] = json.loads(body or b"{}").get("plan")

    print(json.dumps(report, indent=2))
    for note in notes:
        print(f"::notice::{note}", file=sys.stderr)
    for failure in failures:
        print(f"::error::{failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
