"""Open the operator surface the way an operator does, and act on it.

The console was built, served, tested against its view model, and rendered in a
headless browser from the *static* build — and it still answered 401 to anyone
who typed its URL, because it authenticated by a header a browser cannot send
on navigation. The quickstart's own instruction was to `curl` it, which renders
the page and can click nothing on it. Every check passed; nobody had opened it.

So this drives a real browser against a real server against a real database:
type the URL, get the door, paste the key, land in the console, and click the
one button a partner must click after signup. Nothing here inspects a view
model — only what a person can see and press.

    ZOLTS_DATABASE_URL=… ZOLTS_SECRET_KEY=… python3 scripts/browser_console.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PORT = int(os.environ.get("ZOLTS_BROWSER_PORT", "8199"))
BASE = f"http://127.0.0.1:{PORT}"
CHROME = os.environ.get("ZOLTS_CHROME_PATH")


def _wait_for_health(deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE}/health", timeout=2):
                return
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    raise SystemExit("the API never became healthy")


def _seed() -> tuple[str, str]:
    """A tenant that signed up the way a partner does: invitation, drafts."""
    from runtime import onboarding
    from runtime.db import Database

    db = Database(os.environ["ZOLTS_DATABASE_URL"],
                  os.environ.get("ZOLTS_APP_DATABASE_URL"))
    db.migrate()
    db.grant_app_role()
    invitation = onboarding.mint(db, company_name=f"Browser Co {uuid.uuid4().hex[:6]}",
                                 blueprint_id="b2b-saas-sales-led")
    signed_up = onboarding.redeem(db, invitation.token)
    db.close()
    drafts = [p for p in signed_up["programs"] if p["status"] == "draft"]
    if not drafts:
        raise SystemExit("signup published no drafts, so there is nothing to activate")

    # One proposal waiting for a person, in the shape the agent layer emits.
    # Seeded rather than generated: this check is about the surface, and
    # calling a model to produce a draft would make it a model test.
    tenant_id = signed_up["tenant"]["id"]
    db = Database(os.environ["ZOLTS_DATABASE_URL"],
                  os.environ.get("ZOLTS_APP_DATABASE_URL"))
    with db.tenant_tx(tenant_id) as cur:
        cur.execute(
            "insert into proposal (tenant_id, agent, idempotency_key, channel, step_key,"
            " model, prompt_version, content, evidence, eval, eval_score, spend,"
            " cost_micros, state, gate_reason)"
            " values (%s,'copywriter',%s,'email','email_1','claude-haiku-4-5-20251001',"
            " 'copywriter-v3', %s, %s, %s, 0.81, %s, 10400, 'needs_human',"
            " 'eval 0.81 below the 0.85 auto-send threshold')",
            (tenant_id, f"browser-{uuid.uuid4().hex}",
             json.dumps({"body": "Congratulations on the round.",
                         "dropped_claims": ["They are hiring 40 engineers."]}),
             json.dumps([{"ref": "e1", "source": "filing", "text": "A $12m Series A."}]),
             json.dumps({"failures": []}), json.dumps({"verdict": "yes"})))
    db.close()
    return signed_up["api_key"], tenant_id


def _chromium(pw):
    return pw.chromium.launch(executable_path=CHROME) if CHROME else pw.chromium.launch()


def main() -> int:
    from playwright.sync_api import sync_playwright

    api_key, tenant_id = _seed()
    server = subprocess.Popen(
        [sys.executable, "-m", "runtime.cli", "serve", "--host", "127.0.0.1",
         "--port", str(PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)})
    try:
        _wait_for_health(time.monotonic() + 60)
        report: dict[str, object] = {}
        errors: list[str] = []
        violations: list[str] = []

        with sync_playwright() as pw:
            browser = _chromium(pw)
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: violations.append(m.text)
                    if "Content Security Policy" in m.text else None)

            # 1. Somebody types the URL. They must get a door, not a 401 body.
            page.goto(f"{BASE}/console")
            page.wait_for_selector("#f", timeout=15_000)
            report["door_shown"] = True

            # 2. They paste the key they were given at signup.
            page.fill("#k", api_key)
            page.click("button[type=submit]")
            # `.app` is in the static markup, so waiting on it resolves before
            # the page's script has run. Wait for something the script
            # produces, or the assertions race the render.
            page.wait_for_selector("#activate", timeout=15_000)
            report["console_opened"] = True
            report["tenant_rendered"] = page.locator("#ws-name").inner_text()

            # 3. The first action after signup: activate the draft.
            report["activate_offered"] = page.locator("#activate").inner_text()
            page.click("#activate")
            page.wait_for_selector("#activate", state="detached", timeout=15_000)
            page.wait_for_selector(".dactions .btn-p", timeout=15_000)
            report["after_activate"] = page.locator(".dactions .btn-p").first.inner_text()

            # 4. The review queue: agents propose, a person disposes. Until
            #    this existed the disposing was curl, and the rail counted a
            #    queue that led nowhere.
            page.click('nav a[data-view="review"]')
            page.wait_for_selector("#approve", timeout=15_000)
            report["queue_rendered"] = page.locator("#list .row").count()
            report["gate_reason_shown"] = "Gate" in page.locator(".dhead, .block").first \
                .evaluate("el => el.parentElement.innerText")
            page.click("#approve")
            page.wait_for_selector("#approve", state="detached", timeout=15_000)

            report["page_errors"] = errors
            report["csp_violations"] = violations
            browser.close()

        # 4. The claim the button makes, checked where it has to be true.
        from runtime.db import Database

        db = Database(os.environ["ZOLTS_DATABASE_URL"],
                      os.environ.get("ZOLTS_APP_DATABASE_URL"))
        with db.tenant_tx(tenant_id) as cur:
            cur.execute("select key, status from program order by key")
            report["programs_in_database"] = [dict(r) for r in cur.fetchall()]
            cur.execute("select state, approved_by from proposal order by created_at")
            report["proposals_in_database"] = [dict(r) for r in cur.fetchall()]
        db.close()

        print(json.dumps(report, indent=2))
        live = [p for p in report["programs_in_database"] if p["status"] == "live"]
        if not live:
            print("::error::the button reported success and no program went live",
                  file=sys.stderr)
            return 1
        decided = [p for p in report["proposals_in_database"]
                   if p["state"] not in ("draft", "needs_human")]
        if report.get("queue_rendered") and not decided:
            print("::error::Approve reported success and no proposal was decided",
                  file=sys.stderr)
            return 1
        if errors or violations:
            print("::error::the console raised errors or CSP violations", file=sys.stderr)
            return 1
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
