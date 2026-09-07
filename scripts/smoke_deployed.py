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

On a host where the worker is a cron-invoked function (ADR-041), `/api/tick`
must be locked to a stranger: 401 without the cron's bearer, never 200. With
`CRON_SECRET` in the environment — never on the command line — it also
invokes one tick and requires it to run, which is the release check for the
worker on that host.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT = 15


def _get(url: str, key: str | None = None,
         bearer: str | None = None) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, headers={"user-agent": "zolts-smoke"})
    if key:
        request.add_header("x-api-key", key)
    if bearer:
        request.add_header("authorization", f"Bearer {bearer}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _unconfigured(body: bytes) -> bool:
    try:
        return json.loads(body or b"{}").get("status") == "unconfigured"
    except ValueError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="base URL of the deployment")
    parser.add_argument("--key", help="an API key, to check a tenant-scoped read")
    parser.add_argument("--allow-insecure", action="store_true",
                        help="permit http:// — for a local rehearsal only")
    parser.add_argument("--strict", action="store_true",
                        help="fail on any liveness signal, not only on a broken "
                             "deployment. For a run after the tenant is configured")
    parser.add_argument("--expect-migrations", metavar="DIR",
                        help="fail unless /health reports every migration in this "
                             "directory as applied. A release whose schema is behind "
                             "its code answers 200 and then 500s on the new table")
    parser.add_argument("--allow-unconfigured", action="store_true",
                        help="accept a runtime that answers 503 'unconfigured' on "
                             "every path: the surface is released, the database "
                             "is not yet. For the release before the secrets exist")
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
    if status == 503 and args.allow_unconfigured and _unconfigured(body):
        # The function is there and says what it is missing, which is what
        # this run was asked to accept. Nothing below can answer yet.
        report["unconfigured"] = json.loads(body).get("detail")
        print(json.dumps(report, indent=2))
        print("::notice::the runtime is released and unconfigured: "
              f"{report['unconfigured']}", file=sys.stderr)
        return 0
    if status != 200:
        failures.append(f"/health answered {status}")
    else:
        report["health_body"] = json.loads(body or b"{}")
        if args.expect_migrations:
            on_disk = sorted(p.stem for p in Path(args.expect_migrations).glob("*.sql"))
            applied = set(report["health_body"].get("migrations") or [])
            pending = [m for m in on_disk if m not in applied]
            report["pendingMigrations"] = pending
            if pending:
                failures.append(f"the running code carries {len(pending)} migrations "
                                f"the database has not applied: {', '.join(pending)}")

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

    # The front door. On Vercel `/` is the static demo, served before the
    # rewrite that sends everything else to the function; a host with no
    # static site answers 404 from the API. Anything else is a router that
    # sends the demo's path to the function, or the function's paths nowhere.
    status, _, body = _get(f"{base}/")
    report["root"] = status
    if status not in (200, 404):
        failures.append(f"/ answered {status}")
    elif status == 200 and b"<title>" not in body:
        failures.append("/ answered 200 with something that is not a page")

    # The scheduled routes, to a stranger. Either absent — a host that runs
    # the worker as a process — or locked. 200 is a public URL that drains the
    # outbox; 503 is a deployment that never set the secret; anything else is
    # a broken function.
    status, _, body = _get(f"{base}/api/tick")
    report["tick"] = status
    if status == 404:
        notes.append("no /api/tick: this host runs the worker as a process")
    elif status == 503:
        (failures if args.strict else notes).append(
            "/api/tick answers 503: CRON_SECRET is not set, so the cron runs nothing")
    elif status != 401:
        failures.append(f"/api/tick answered {status} to a request with no cron secret")

    # With the secret — from the environment, never an argument, because an
    # argument is in the shell history — one real tick. This is the check
    # that the worker runs on this host at all.
    secret = os.environ.get("CRON_SECRET")
    if secret and status == 401:
        status, _, body = _get(f"{base}/api/tick", bearer=secret)
        report["tickRan"] = status
        if status != 200:
            failures.append(f"/api/tick answered {status} to the cron secret")
        else:
            ran = json.loads(body or b"{}")
            report["tickPasses"] = ran.get("passes")
            report["tickClaimed"] = ran.get("claimed")
            if not ran.get("ran"):
                failures.append("/api/tick accepted the secret and ran nothing")

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
