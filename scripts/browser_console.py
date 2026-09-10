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

        # A sending fleet, so the surface renders rows rather than only its
        # empty state. Two mailboxes on one authenticated domain, one of them
        # part-way through warm-up, because a capacity that is not the base cap
        # is the number a wrong warm-up calculation would get wrong.
        cur.execute(
            "insert into sending_domain (tenant_id, name, spf, dkim, dmarc_policy,"
            " one_click_unsubscribe) values (%s,'outbound.example',true,true,"
            " 'quarantine',true)", (tenant_id,))
        for address, provider, age in (("ae@outbound.example", "google", 45),
                                       ("sdr@outbound.example", "other", 5)):
            cur.execute(
                "insert into mailbox (tenant_id, address, domain, provider,"
                " warmup_started_on) values (%s,%s,'outbound.example',%s,"
                " current_date - %s)", (tenant_id, address, provider, age))
    db.close()
    return signed_up["api_key"], tenant_id


def _chromium(pw):
    return pw.chromium.launch(executable_path=CHROME) if CHROME else pw.chromium.launch()


# Where the *text* starts, not where the box does. A cell with left padding
# overlaps the status dot's box and draws nowhere near it, so comparing element
# boxes reports every row in the console. A `Range` over the cell's contents
# measures the glyphs, which is what a reader sees covered.
DRAWN_OVER = """
() => {
  const textBox = (el) => {
    const range = document.createRange();
    range.selectNodeContents(el);
    const r = range.getBoundingClientRect();
    return (r.width || r.height) ? r : null;
  };
  const hits = [];
  for (const row of document.querySelectorAll('.row')) {
    const marker = row.querySelector(':scope > .st, :scope > .sig');
    if (!marker) continue;
    const m = marker.getBoundingClientRect();
    for (const cell of row.querySelectorAll(':scope > *')) {
      if (cell === marker || !cell.textContent.trim()) continue;
      const t = textBox(cell);
      if (!t) continue;
      const ox = Math.min(m.right, t.right) - Math.max(m.left, t.left);
      const oy = Math.min(m.bottom, t.bottom) - Math.max(m.top, t.top);
      if (ox > 1 && oy > 1) hits.push(cell.textContent.trim().slice(0, 40));
    }
  }
  return hits.slice(0, 8);
}
"""

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

            # 3a. The activation answered with a note — this tenant has no
            #     baseline — and the console's reload used to discard it.
            #     Decision 35 says noted where an operator looks, and this is
            #     where they look (D-43).
            page.wait_for_selector("#activation-note", timeout=15_000)
            report["activation_note"] = page.locator("#activation-note").inner_text()

            # 3a-bis. The CFO's half of the same screen. A tenant on its first
            #         day has no frozen report, and the panel has to say that
            #         rather than be absent — the operator running an
            #         onboarding is the person who needs to know one is coming
            #         (ADR-043).
            frozen = page.locator("div.block", has_text="Frozen reports").first
            report["frozen_reports_panel"] = frozen.inner_text()[:200]

            # 3b. ADR-002 said the UI generates DSL, and the button that
            #     promised it was labelled "Open in editor" and did nothing.
            #     This drives the real one: open it, change the holdout, and
            #     publish. What the form emits is a whole document, so the
            #     check that matters is that the *server* accepts it — the
            #     same schema, linter and holdout rule as `validate.py`.
            page.click("#tunebtn")
            page.wait_for_selector("#dsl", timeout=15_000)
            report["editor_fields"] = page.locator(".field input, .field select").count()
            generated = json.loads(page.locator("#dsl").inner_text())
            report["generated_version"] = generated["metadata"]["version"]
            # A whole document, not a patch: what the form does not tune has
            # to survive, or publishing would quietly drop half the program.
            report["generated_keeps_audience"] = bool(
                (generated["spec"].get("audience") or {}).get("sql"))
            page.fill("#c-spec-experiment-holdout_pct", "12")
            page.wait_for_timeout(200)
            report["publish_offered"] = not page.is_disabled("#pub")
            # A successful publish reloads the page. `#tunebtn` is in the DOM
            # before and after it, so waiting on that selector resolved about
            # sixty milliseconds after the click — against the document the
            # reload was already replacing. Every later step then ran on a page
            # with a navigation in flight underneath it, and the review queue's
            # Approve button was torn out from under a click that had already
            # started (D-96). Wait for the reload itself: a publish the server
            # refuses never navigates, so it fails here, naming this step,
            # instead of surfacing three steps later as a click that hangs.
            with page.expect_navigation(wait_until="load", timeout=45_000):
                page.click("#pub")
            page.wait_for_selector("#tunebtn", timeout=20_000)
            report["publish_reloaded"] = True

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

            # 5. The sending fleet. This is the surface whose errors do not
            #    surface as a failing test: a burned domain shows up weeks
            #    later, so an operator has to be able to see the state before
            #    then. A fresh tenant has no fleet, and the honest answer is
            #    that capacity is not managed rather than that it is zero.
            page.click('nav a[data-view="sending"]')
            page.wait_for_selector("#list .empty, #list .row", timeout=15_000)
            report["sending_rail"] = page.locator("#nav-sending").inner_text()
            report["sending_rows"] = page.locator("#list .row").count()
            report["sending_view"] = page.locator("#list").inner_text()[:200]

            # 6. The three views that did not exist, and the rail that
            #    offered five links leading nowhere. Every entry is clicked,
            #    because a nav that promises what it cannot do is the defect
            #    this checks for rather than a cosmetic one.
            report["rail"] = [t.replace("\n", " ")
                              for t in page.locator("nav a").all_inner_texts()]
            report["dead_links"] = page.locator("nav a:not([data-view])").count()

            # Every view, not three, and measured rather than eyeballed. Two
            # things went wrong here that no test could see: five views wore
            # the Programs header over columns sized for a different table, so
            # values wrapped and rows grew into each other; and a sentence in
            # a property list's value column, which is nowrap, printed over
            # its own label and ran off the panel.
            #
            # A row taller than its declared height means a cell wrapped. A
            # panel element wider than its box means a value is clipped or
            # overlapping. Neither is a matter of taste.
            wrapped, clipped, tiles, narrow = [], [], {}, []
            for entry in page.locator("nav a[data-view]").all():
                view = entry.get_attribute("data-view")
                entry.click()
                page.wait_for_selector("#list .row, #list .empty", timeout=15_000)
                # A row is one line unless the view's own head spec declares a
                # prose column with "w:", in which case it may reach two. The
                # allowance is read from the same declaration that renders the
                # column, so a view cannot start wrapping quietly.
                tall = page.evaluate(
                    "() => { const prose = !!document.querySelector('.row .cell.w');"
                    " const limit = prose ? 60 : 40;"
                    " return [...document.querySelectorAll('.row')]"
                    "   .filter(r => r.getBoundingClientRect().height > limit).length; }")
                if tall:
                    wrapped.append({"view": view, "rows": tall})
                # `.cell` ellipsises on purpose; the detail panel and the stat
                # tiles never should.
                over = page.evaluate(
                    "() => [...document.querySelectorAll('.detail dt, .detail dd,"
                    " .detail .pnote, .detail .blabel, #kpis .k, #kpis .v, #kpis .u')]"
                    ".filter(e => e.scrollWidth > e.clientWidth + 1)"
                    ".map(e => e.textContent.slice(0, 40))")
                if over:
                    clipped.append({"view": view, "elements": over})
                # The tiles are the first thing read. A view that sets none
                # after another set some would show the previous view's
                # numbers, confidently and wrongly.
                tiles[view] = page.evaluate(
                    "() => [...document.querySelectorAll('#kpis .k')]"
                    ".map(e => e.textContent)")
                report[f"{view}_view"] = page.locator("#list").inner_text()[:120]
            # A wide screen. Columns used to be fixed pixel widths, so a
            # 1920px display gave its extra 500px to empty gutter while
            # `local-services-multisite` still ellipsised in a 152px column.
            # Where there is width to spare, the text gets it: at 1920 no cell
            # may be cut off. Prose columns are exempt — they are declared, and
            # they wrap rather than clip.
            page.set_viewport_size({"width": 1920, "height": 1080})
            page.wait_for_timeout(250)
            cut, stiff = [], []
            for entry in page.locator("nav a[data-view]").all():
                view = entry.get_attribute("data-view")
                entry.click()
                page.wait_for_timeout(150)
                over = page.evaluate(
                    "() => [...document.querySelectorAll('.row > *')]"
                    ".filter(e => !e.classList.contains('w')"
                    "          && e.scrollWidth > e.clientWidth + 1)"
                    ".map(e => e.textContent.slice(0, 40))")
                if over:
                    cut.append({"view": view, "cells": over})
                # The exemption above is earned, not assumed.
                rigid = page.evaluate(
                    "() => [...document.querySelectorAll('.row .cell.w')]"
                    ".filter(e => getComputedStyle(e).whiteSpace === 'nowrap')"
                    ".map(e => e.textContent.slice(0, 40))")
                if rigid:
                    stiff.append({"view": view, "cells": rigid})
            report["cut_off_on_a_wide_screen"] = cut
            report["prose_that_does_not_wrap"] = stiff

            # A phone. Below 820px the rail is a strip of destinations and the
            # rows stack into label/value pairs — it used to be `display:none`
            # and four columns clipped off the right edge, so the console was
            # one screen with half a table on it.
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(250)
            drawn_over: list[dict[str, object]] = []
            narrow.append({"nav_reachable": page.evaluate(
                "() => { const r = document.querySelector('.rail');"
                " return !!r && getComputedStyle(r).display !== 'none'"
                "   && document.querySelectorAll('nav a[data-view]').length > 1; }")})
            for entry in page.locator("nav a[data-view]").all():
                view = entry.get_attribute("data-view")
                entry.click()
                page.wait_for_timeout(150)
                over = page.evaluate(
                    "() => [...document.querySelectorAll('.cell, .bp, .n, .lift,"
                    " #kpis .k, #kpis .v, #kpis .u')]"
                    ".filter(e => e.scrollWidth > e.clientWidth + 1).length")
                if over:
                    narrow.append({"view": view, "clipped": over})
                # Nothing may be drawn on top of text. Three collisions lived
                # here and every existing check passed through all of them:
                # nothing was clipped, nothing overflowed, and a mailbox
                # rendered as `e@outbound.example` because the status dot sat
                # on the first character of the address. D-38.
                covered = page.evaluate(DRAWN_OVER)
                if covered:
                    drawn_over.append({"view": view, "text": covered})
            narrow.append({"horizontal_overflow": page.evaluate(
                "() => document.documentElement.scrollWidth - window.innerWidth")})
            page.set_viewport_size({"width": 1440, "height": 900})

            report["rows_that_wrapped"] = wrapped
            report["clipped_in_panel"] = clipped
            report["on_a_phone"] = narrow
            report["drawn_over_text_on_a_phone"] = drawn_over
            report["stat_tiles"] = tiles
            # Two views may share a label; they must not share the whole set.
            seen, repeated = {}, []
            for view, labels in tiles.items():
                key = "|".join(labels)
                if key and key in seen:
                    repeated.append([seen[key], view])
                seen[key] = view
            report["views_sharing_tiles"] = repeated

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
            cur.execute("select key, version, spec->'experiment'->>'holdout_pct' as holdout"
                        " from program order by created_at desc limit 1")
            report["newest_program"] = dict(one) if (one := cur.fetchone()) else None
            cur.execute("select state, approved_by from proposal order by created_at")
            report["proposals_in_database"] = [dict(r) for r in cur.fetchall()]
        db.close()

        print(json.dumps(report, indent=2))
        phone = report["on_a_phone"]
        if not phone[0].get("nav_reachable") or any("clipped" in e for e in phone) \
                or phone[-1].get("horizontal_overflow"):
            print("the console is not usable at 390px", file=sys.stderr)
            return 1
        if "period" not in report.get("frozen_reports_panel", "").lower():
            print("the Frozen reports panel does not say when one appears:",
                  report.get("frozen_reports_panel"), file=sys.stderr)
            return 1
        if report["views_sharing_tiles"]:
            print("two views showed the same stat tiles", file=sys.stderr)
            return 1
        if report["cut_off_on_a_wide_screen"]:
            print("a 1920px screen still cuts text off:",
                  json.dumps(report["cut_off_on_a_wide_screen"])[:400], file=sys.stderr)
            return 1
        if report["prose_that_does_not_wrap"]:
            print("a column declared as prose still clips:",
                  json.dumps(report["prose_that_does_not_wrap"])[:400], file=sys.stderr)
            return 1
        if report["rows_that_wrapped"] or report["clipped_in_panel"]:
            print("a row grew past its height, or a value ran outside its box",
                  file=sys.stderr)
            return 1
        if report["drawn_over_text_on_a_phone"]:
            print("something is drawn on top of text at 390px:",
                  json.dumps(report["drawn_over_text_on_a_phone"])[:400], file=sys.stderr)
            return 1
        if "baseline" not in (report.get("activation_note") or ""):
            print("the activation answered with a note and the console did not show it:",
                  json.dumps(report.get("activation_note")), file=sys.stderr)
            return 1
        # The document the browser built is in the database, at the version it
        # bumped to and carrying the value that was typed. Anything less and
        # "the UI generates DSL" is a sentence in a document again.
        newest = report.get("newest_program") or {}
        if newest.get("version") != report.get("generated_version"):
            print("::error::the console published no new version:", json.dumps(newest),
                  "expected", report.get("generated_version"), file=sys.stderr)
            return 1
        if newest.get("holdout") != "12":
            print("::error::the published version does not carry the edited holdout:",
                  json.dumps(newest), file=sys.stderr)
            return 1
        if not report.get("generated_keeps_audience"):
            print("::error::the generated document dropped the audience it did not tune",
                  file=sys.stderr)
            return 1

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
