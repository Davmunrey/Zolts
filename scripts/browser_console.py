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
import re
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

        # Two more drafts waiting for a person, so a batch has something to
        # act on after the single approve above has taken the first: bulk,
        # with reasons (docs/28, OX-7).
        for i in range(2):
            cur.execute(
                "insert into proposal (tenant_id, agent, idempotency_key, channel, step_key,"
                " model, prompt_version, content, evidence, eval, eval_score, spend,"
                " cost_micros, state, gate_reason)"
                " values (%s,'copywriter',%s,'email','email_2','claude-haiku-4-5-20251001',"
                " 'copywriter-v3', %s, '[]', %s, 0.80, %s, 9000, 'needs_human',"
                " 'eval 0.80 below the 0.85 auto-send threshold')",
                (tenant_id, f"browser-batch-{i}-{uuid.uuid4().hex}",
                 json.dumps({"body": f"Following up on the round, draft {i + 1}.",
                             "dropped_claims": []}),
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

        # One human task, past its deadline. A step on the task channel is
        # work the runtime cannot do and cannot close, so a person has to see
        # it and say it is done. Seeded past due on purpose: the empty state
        # proves the screen exists, and only a row proves it works.
        cur.execute(
            "insert into touch (tenant_id, channel, step_key, idempotency_key,"
            " status, content, due_at, direction)"
            " values (%s,'task','call_revops',%s,'queued',%s,"
            " now() - interval '3 hours','out')",
            (tenant_id, f"task-{uuid.uuid4().hex}",
             json.dumps({"awaiting": "human_review",
                         "brief": "Call the RevOps lead about the new role."})))

        # One contact with nothing on them, so a row on Prospects is buyable.
        # Seeded incomplete on purpose: the empty state proves the screen
        # exists, and only a row an operator can spend money on proves the
        # control does.
        cur.execute(
            "insert into person (tenant_id, full_name, country)"
            " values (%s,'Dana Reyes','ES')", (tenant_id,))

        # One contact with a whole story: enrolled, a policy decision that
        # allowed the send under a named rule from a versioned pack, a
        # proposal with evidence and a claim removed for having none, a touch
        # that went out. Six tables, one person. The screen has to read all
        # six back, or it is the defect this repository keeps finding.
        cur.execute("select id from program where status = 'draft' limit 1")
        program_id = cur.fetchone()["id"]
        cur.execute(
            "insert into person (tenant_id, full_name, email, country)"
            " values (%s,'Iker Sanz','iker@northbeam.example','ES') returning id",
            (tenant_id,))
        iker = cur.fetchone()["id"]
        cur.execute(
            "insert into enrollment (tenant_id, program_id, entity_type,"
            " entity_id, variant, tier, state, entered_at)"
            " values (%s,%s,'person',%s,'treatment','A','active',"
            " now() - interval '2 days') returning id", (tenant_id, program_id, iker))
        enrol = cur.fetchone()["id"]
        cur.execute(
            "insert into policy_decision (tenant_id, subject_type, subject_id,"
            " action, decision, rule_key, jurisdiction, rationale, pack_version,"
            " pack_digest, decided_at) values (%s,'person',%s,'email.send','allow',"
            " 'b2b.legitimate_interest','ES','business contact at a company "
            "account, outside quiet hours','1.0.0',"
            " 'f00dfeedcafe0123456789abcdef', now() - interval '1 day')",
            (tenant_id, iker))
        cur.execute(
            "insert into proposal (tenant_id, enrollment_id, program_id, agent,"
            " step_key, channel, idempotency_key, model, prompt_version, content,"
            " evidence, eval, eval_score, spend, cost_micros, state, gate_reason,"
            " created_at) values (%s,%s,%s,'copywriter','email_1','email',%s,"
            " 'claude-haiku-4-5-20251001','copywriter-v3',%s,%s,%s,0.91,%s,8200,"
            " 'approved','eval 0.91 above the auto-send threshold',"
            " now() - interval '1 day')",
            (tenant_id, enrol, program_id, f"why-{uuid.uuid4().hex}",
             json.dumps({"body": "Saw the Series A. Congratulations.",
                         "dropped_claims": ["They plan to open a Lisbon office."]}),
             json.dumps([{"ref": "e1", "source": "press release",
                          "text": "Northbeam raises a $12m Series A."}]),
             json.dumps({"failures": []}), json.dumps({"verdict": "yes"})))
        # Replied to, and read as positive: the one email that went out is
        # also the one row on "which copy works", where a single positive
        # reply is a count and not a rate (docs/28, OX-4).
        cur.execute(
            "insert into touch (tenant_id, enrollment_id, person_id, channel,"
            " direction, step_key, idempotency_key, status, content, provider,"
            " cost_micros, sent_at) values (%s,%s,%s,'email','out','email_1',%s,"
            " 'replied','{}','smartlead',1200, now() - interval '1 day')",
            (tenant_id, enrol, iker, f"why-touch-{uuid.uuid4().hex}"))
        cur.execute(
            "insert into outcome (tenant_id, enrollment_id, type, occurred_at, source,"
            " verified_by) values (%s,%s,'reply_positive', now() - interval '20 hours',"
            " 'smartlead','triage')", (tenant_id, enrol))

        # The signal that made that enrolment, and the outcome it produced,
        # so the Signals screen has one catalogue signal that earned its
        # keep beside eight that never fired. The outcome is the first event
        # of the metric the programme declares, read rather than guessed,
        # because a conversion under the wrong metric is not counted and the
        # screen would read as a funnel that stops at "reached".
        from zolts import metrics

        cur.execute("select spec from program where id = %s", (program_id,))
        declared = ((cur.fetchone()["spec"] or {}).get("experiment") or {}).get("primary_metric")
        metric = metrics.resolve(declared)
        cur.execute(
            "insert into signal (tenant_id, entity_type, entity_id, type, strength,"
            " half_life_h, source, legal_basis, payload, observed_at, ingested_at)"
            " values (%s,'person',%s,'hiring.role_opened',0.6,720,'jobs_feed',"
            " 'legitimate_interest','{}', now() - interval '2 days',"
            " now() - interval '2 days' + interval '3 minutes') returning id",
            (tenant_id, iker))
        fired = cur.fetchone()["id"]
        cur.execute("update enrollment set context = %s where id = %s",
                    (json.dumps({"signal_id": str(fired)}), enrol))
        cur.execute(
            "insert into outcome (tenant_id, enrollment_id, type, occurred_at, source)"
            " values (%s,%s,%s, now() - interval '12 hours','crm')",
            (tenant_id, enrol, metric.events[0]))

        # One action that gave up, with the error the runbook's own triage
        # table keys on. Seeded dead on purpose: an empty state proves the
        # screen exists, and only a row proves an operator can act on it.
        cur.execute(
            "insert into action (tenant_id, kind, channel, step_key,"
            " idempotency_key, payload, state, attempts, max_attempts,"
            " last_error) values (%s,'send','email','email_1',%s,%s,'dead',5,5,"
            " '401 the provider refused the credential: invalid_grant')",
            (tenant_id, f"dead-{uuid.uuid4().hex}", json.dumps({"step": {}})))
    db.close()
    return signed_up["api_key"], tenant_id


def _seed_for_the_phone(tenant_id: str) -> tuple[str, str]:
    """A draft and a task for the phone, seeded after the desktop steps took
    the first ones, and returned by id so the read-back is by id."""
    from runtime.db import Database

    db = Database(os.environ["ZOLTS_DATABASE_URL"],
                  os.environ.get("ZOLTS_APP_DATABASE_URL"))
    try:
        with db.tenant_tx(tenant_id) as cur:
            cur.execute(
                "insert into proposal (tenant_id, agent, idempotency_key, channel, step_key,"
                " model, prompt_version, content, evidence, eval, eval_score, spend,"
                " cost_micros, state, gate_reason)"
                " values (%s,'copywriter',%s,'email','email_1','claude-haiku-4-5-20251001',"
                " 'copywriter-v3', %s, '[]', %s, 0.79, %s, 8800, 'needs_human',"
                " 'eval 0.79 below the 0.85 auto-send threshold') returning id",
                (tenant_id, f"browser-phone-{uuid.uuid4().hex}",
                 json.dumps({"body": "Read on a phone at eleven at night.",
                             "dropped_claims": []}),
                 json.dumps({"failures": []}), json.dumps({"verdict": "yes"})))
            proposal_id = str(cur.fetchone()["id"])
            cur.execute(
                "insert into touch (tenant_id, channel, step_key, idempotency_key,"
                " status, content, due_at, direction)"
                " values (%s,'task','call_revops',%s,'queued',%s,"
                " now() + interval '2 hours','out') returning id",
                (tenant_id, f"task-phone-{uuid.uuid4().hex}",
                 json.dumps({"awaiting": "human_review",
                             "brief": "Confirm the call from the road."})))
            task_id = str(cur.fetchone()["id"])
    finally:
        db.close()
    return proposal_id, task_id


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
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
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
            page.wait_for_selector("#goto", timeout=15_000)
            report["console_opened"] = True
            report["tenant_rendered"] = page.locator("#ws-name").inner_text()

            # 2b. The console opens on the work, not on the inventory. A tenant
            #     one minute past signup has a programme published and never
            #     activated, and that is the thing to do first — so it has to
            #     be on this screen, ranked, with the cost of leaving it, and
            #     the button has to lead to the screen where it is done.
            report["today_rail"] = page.locator("#nav-today").inner_text()
            report["today_list"] = page.locator("#list").inner_text()[:500]
            # Ranked by what ignoring it costs, so a broken promise outranks a
            # programme that has not started. Read off the screen rather than
            # assumed: the order is the product decision this view exists for.
            report["today_order"] = [
                r.inner_text().split("\n")[0]
                for r in page.locator("#list .row").all()]
            page.locator("#list .row", has_text="never activated").first.click()
            report["today_detail"] = page.locator("#detail").inner_text()[:400]
            page.click("#goto")
            page.wait_for_selector("#activate", timeout=15_000)

            # 2c. What activating it would do today, before the click. The
            #     number comes from the functions that will enrol, with the
            #     insert taken out; an audience the runtime cannot evaluate
            #     says so rather than reading as zero.
            page.wait_for_selector("#pv dl, #pv-unanswerable, #pv-error", timeout=20_000)
            report["preview_before_activate"] = page.locator("#pv").inner_text()[:500]
            # Which copy works, on the same panel: the seeded email was
            # replied to and read as positive, so the step shows one sent and
            # a count with no rate, under the floor.
            report["copy_panel"] = page.locator("#detail").inner_text()[:2400]

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
            # A successful publish no longer reloads the page (docs/28, OX-6):
            # the console re-reads its data and the new version appears in
            # the list in place. Wait for that, not for a navigation — and a
            # publish the server refuses shows its reason in `#pubnote`, so a
            # missing version names this step rather than surfacing later.
            page.evaluate("() => { window.__zoltsPage = 1; }")
            page.click("#pub")
            try:
                page.wait_for_function(
                    "v => document.querySelector('#list').innerText.includes(v)",
                    arg=report["generated_version"], timeout=45_000)
            except PlaywrightTimeout:
                note = page.locator("#pubnote").inner_text() if page.locator("#pubnote").count() else ""
                print("::error::the published version never appeared in the list. "
                      "The page says:", json.dumps(note or "(nothing: the request never returned)"),
                      file=sys.stderr)
                return 1
            page.wait_for_selector("#tunebtn", timeout=20_000)
            report["publish_in_place"] = page.evaluate("() => window.__zoltsPage === 1")

            # 4. The review queue: agents propose, a person disposes. Until
            #    this existed the disposing was curl, and the rail counted a
            #    queue that led nowhere.
            page.click('nav a[data-view="review"]')
            page.wait_for_selector("#approve", timeout=15_000)
            report["queue_rendered"] = page.locator("#list .row").count()
            report["gate_reason_shown"] = "Gate" in page.locator(".dhead, .block").first \
                .evaluate("el => el.parentElement.innerText")
            # Approve, and watch the rail count fall without a navigation: the
            # marker set on the window before the click survives a re-render
            # and dies with a reload (docs/28, OX-6).
            before = page.locator("#nav-review").inner_text()
            page.evaluate("() => { window.__zoltsApprove = 1; }")
            page.click("#approve")
            page.wait_for_function(
                "b => document.getElementById('nav-review') === null"
                "  || document.getElementById('nav-review').innerText !== b",
                arg=before, timeout=15_000)
            report["approve_in_place"] = page.evaluate("() => window.__zoltsApprove === 1")
            report["review_rail_before_after"] = [
                before, page.evaluate("() => (document.getElementById('nav-review') || {}).innerText || '0'")]

            # 4a. Bulk, with reasons. Two drafts remain; a modified click adds
            #     each to the batch, one reason covers both, and a thin reason
            #     is refused by name before anything is approved.
            page.wait_for_selector("#list .row[data-r]", timeout=15_000)
            for row in page.locator("#list .row[data-r]").all():
                row.click(modifiers=["Control"])
            page.wait_for_selector("#batch", timeout=15_000)
            report["batch_offered"] = page.locator("#batch .blabel").inner_text()
            page.fill("#batch-reason", "ok")
            page.click('#batch [data-batch="approve"]')
            page.wait_for_selector("#batch-msg:not([hidden])", timeout=15_000)
            report["thin_batch_reason_refused"] = page.locator("#batch-msg").inner_text()
            page.fill("#batch-reason", "legal cleared the whole series this morning")
            page.click('#batch [data-batch="approve"]')
            page.wait_for_function(
                "() => (document.getElementById('nav-review') || {}).innerText === '0'",
                timeout=20_000)
            report["batch_approved"] = True

            # 4b. The work waiting for a person. `GET /v1/tasks` has answered
            #     this since a human task could be closed at all, and no screen
            #     asked: a queue nobody can see is a queue nobody works, and
            #     the SLA the step declares is then a deadline measured against
            #     nothing.
            page.click('nav a[data-view="tasks"]')
            page.wait_for_selector("#done", timeout=15_000)
            report["tasks_rendered"] = page.locator("#list .row").count()
            report["task_rail"] = page.locator("#nav-tasks").inner_text()
            report["task_detail"] = page.locator("#detail").inner_text()[:400]
            report["task_list"] = page.locator("#list").inner_text()[:300]
            page.click("#done")
            page.wait_for_selector("#done", state="detached", timeout=15_000)

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

            # 5a. The switch nothing in this repository had. `runtime/breakers`
            #     was the only writer of the paused column, so the only actor
            #     that could stop a send was a cut-off firing on rates already
            #     earned, and `docs/09` calls that resource the one whose damage
            #     is not recoverable on the timescale that matters.
            page.locator('#list .row[data-d]').first.click()
            page.wait_for_selector("#sc-act", timeout=15_000)
            report["stop_offered"] = page.locator("#sc-act").inner_text()

            #     A reason that restates the act is refused, and the refusal is
            #     named on the screen rather than shown as a generic failure —
            #     an operator reading "Failed (422)" goes looking for a bug in
            #     the button instead of rewriting their reason.
            page.fill("#sc-reason", "ok")
            page.click("#sc-act")
            page.wait_for_selector("#sc-msg:not([hidden])", timeout=15_000)
            report["thin_reason_refused"] = page.locator("#sc-msg").inner_text()

            page.fill("#sc-reason", "bought list found in the import")
            page.click("#sc-act")
            # The stop re-renders in place (docs/28, OX-6): the button that
            # stopped the domain relabels to the act that starts it again.
            page.wait_for_function(
                "() => (document.getElementById('sc-act') || {}).textContent === 'Let it send'",
                timeout=20_000)
            page.wait_for_selector('#list .row[data-d]', timeout=15_000)
            page.locator('#list .row[data-d]').first.click()
            page.wait_for_selector("#sc-act", timeout=15_000)
            report["after_stop"] = page.locator("#detail").inner_text()[:400]

            # 5a. The palette reaches everything (docs/28, OX-8): the Outbox
            #     from the keyboard, and a contact by name onto their timeline.
            page.keyboard.press("Control+K")
            page.wait_for_selector("#q", timeout=15_000)
            page.fill("#q", "outbox")
            page.keyboard.press("Enter")
            page.wait_for_selector("#ob-revive", timeout=15_000)
            report["palette_reached_outbox"] = page.evaluate(
                "() => document.querySelector('nav a[aria-current=\"page\"]').dataset.view")
            page.keyboard.press("Control+K")
            page.wait_for_selector("#q", timeout=15_000)
            page.fill("#q", "iker")
            page.keyboard.press("Enter")
            page.wait_for_selector("#tl .tlrow", timeout=20_000)
            report["palette_reached_contact"] = page.locator("#detail h2").first.inner_text()

            # 5b. The outbox. The runbook told an operator to requeue a dead
            #     action and supplied a raw SQL update that nulls `last_error`,
            #     destroying the only record of why it died at the moment
            #     somebody is deciding about it. No screen listed them at all.
            page.click('nav a[data-view="outbox"]')
            page.wait_for_selector("#ob-revive", timeout=15_000)
            report["outbox_rail"] = page.locator("#nav-outbox").inner_text()
            report["outbox_rows"] = page.locator("#list .row").count()
            #     The screen reads the runbook's triage off the error rather
            #     than restating it, so an operator is told the credential
            #     expired rather than left to match the string themselves.
            report["outbox_detail"] = page.locator("#detail").inner_text()[:500]

            page.fill("#ob-reason", "retry")
            page.click("#ob-revive")
            page.wait_for_selector("#ob-msg:not([hidden])", timeout=15_000)
            report["thin_outbox_reason_refused"] = page.locator("#ob-msg").inner_text()

            page.fill("#ob-reason", "the token was rotated this morning")
            page.click("#ob-revive")
            page.wait_for_selector('#list .empty', timeout=15_000)
            report["outbox_after_revive"] = page.locator("#list").inner_text()[:200]

            # 5c. Buying a missing field. `POST /v1/enrich` says in its own
            #     docstring that the console sends the rows the operator
            #     selected; the console sent nothing, and the screen that
            #     computed what was missing offered no way to buy any of it.
            page.click('nav a[data-view="prospects"]')
            page.wait_for_selector('#list .row[data-p]', timeout=15_000)
            page.locator('#list .row[data-p]').first.click()
            page.wait_for_selector("#buy-email", timeout=15_000)
            #     The price is on the button, before the click. A phone number
            #     is three times an email and a purchase whose cost appears
            #     only on the invoice is how a data budget disappears.
            report["buy_offered"] = page.locator("#buy-email").inner_text()

            #     Nothing preselects a legal basis, and the button refuses
            #     until one is chosen: it is recorded against every value
            #     bought and cannot be reconstructed later.
            page.click("#buy-email")
            page.wait_for_selector("#buy-msg:not([hidden])", timeout=15_000)
            report["basis_required"] = page.locator("#buy-msg").inner_text()

            page.click('#basis button[data-basis="consent"]')
            page.wait_for_selector('#basis button[aria-pressed="true"]', timeout=15_000)
            report["basis_chosen"] = page.locator(
                '#basis button[aria-pressed="true"]').inner_text()

            # 5d. Why this person. Six tables held the story and no screen
            #     read one contact across all six. The exit criterion in
            #     docs/28 OX-1: read the rule key, the pack digest and a
            #     dropped claim off the screen for a contact who received a
            #     gated send.
            page.locator('#list .row[data-c]', has_text="Iker Sanz").first.click()
            page.wait_for_selector("#tl", timeout=15_000)
            report["timeline_rows"] = page.locator("#tl .tlrow").count()
            report["timeline_kinds"] = [
                r.get_attribute("data-kind") for r in page.locator("#tl .tlrow").all()]
            report["timeline_text"] = page.locator("#tl").inner_text()[:900]

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
                if view == "spend":
                    # docs/08's margin target, on the screen a CFO opens. A
                    # number computed and never rendered is the defect this
                    # script exists for (AGENT-4).
                    report["cost_panel"] = page.locator("#detail").inner_text()[:700]
                if view == "signals":
                    # `docs/06` calls the per-tier p95 the contractual SLA of
                    # two plans, and the console printed the measurement with
                    # no target beside it (D-97). A panel added and never
                    # opened is the defect this whole script exists for, so
                    # the verdict is read off the screen rather than assumed.
                    report["sla_panel"] = page.locator("#detail").inner_text()[:1200]
                    # Which signals earn their keep (docs/28, OX-3): the first
                    # funnel row is the best earner and the panel opens on it.
                    funnel_rows = page.locator("#list .row[data-f]")
                    report["funnel_count"] = funnel_rows.count()
                    report["funnel_rows"] = [
                        row.inner_text() for row in funnel_rows.all()[:3]]
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
            # The phone approves (docs/28, OX-9). The person who unblocks the
            # queue at 11pm is on a phone. The desktop steps took every seeded
            # draft and the task, so a fresh draft and a fresh task are seeded
            # now, and the console is opened again at 390px the way a phone
            # opens it: a load, the remembered view, the rail as a strip.
            phone_proposal, phone_task = _seed_for_the_phone(tenant_id)
            page.reload()
            page.wait_for_function(
                "() => (document.getElementById('nav-review') || {}).innerText === '1'",
                timeout=20_000)
            page.click('nav a[data-view="review"]')
            page.wait_for_selector("#list .row[data-r]", timeout=15_000)
            page.click("#list .row[data-r]")
            # A row tapped at the top of a phone changed something below the
            # fold, and the tap looked like nothing. The panel with the
            # decision on it has to come to the thumb.
            page.wait_for_function(
                "() => { const r = document.getElementById('detail').getBoundingClientRect();"
                " return r.top < innerHeight * 0.6 && r.bottom > 0; }", timeout=10_000)
            report["phone_panel_in_view"] = True
            report["phone_panel_clipped"] = page.evaluate(
                "() => [...document.querySelectorAll('#detail .props dd, #detail .pnote,"
                " #detail .draft, #detail h2')]"
                ".filter(e => e.scrollWidth > e.clientWidth + 1)"
                ".map(e => e.textContent.slice(0, 40))")
            report["phone_approve_height"] = page.evaluate(
                "() => document.getElementById('approve').getBoundingClientRect().height")
            page.click("#approve")
            page.wait_for_function(
                "() => (document.getElementById('nav-review') || {}).innerText === '0'",
                timeout=20_000)
            page.click('nav a[data-view="tasks"]')
            page.wait_for_selector("#done", timeout=15_000)
            report["phone_done_height"] = page.evaluate(
                "() => document.getElementById('done').getBoundingClientRect().height")
            page.click("#done")
            page.wait_for_selector("#done", state="detached", timeout=15_000)
            report["phone_horizontal_overflow"] = page.evaluate(
                "() => document.documentElement.scrollWidth - window.innerWidth")
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
            # Read back rather than trusted: the button reported success once
            # before for a proposal and the row had not moved.
            cur.execute("select count(*) as n from touch where channel = 'task'"
                        "  and completed_at is not null and completed_by is not null")
            report["task_completed"] = int(cur.fetchone()["n"])
            # The phone approves (docs/28, OX-9): the draft and the task seeded
            # for the phone, read back by id rather than trusted to the screen.
            cur.execute("select state from proposal where id = %s", (phone_proposal,))
            report["phone_approved"] = row["state"] if (row := cur.fetchone()) else None
            cur.execute("select completed_at is not null as done from touch where id = %s",
                        (phone_task,))
            report["phone_task_done"] = bool(row["done"]) if (row := cur.fetchone()) else False
            # The stop, where it has to be true. A button that reported success
            # over a row that never moved is a defect this check has caught
            # before, so the column and the audit row are both read back.
            cur.execute("select paused, paused_reason from sending_domain"
                        " where name = 'outbound.example'")
            report["domain_stopped"] = dict(row) if (row := cur.fetchone()) else None
            cur.execute("select actor, subject, detail from audit_log"
                        " where action = 'sending.domain.paused'")
            report["stop_audited"] = [dict(r) for r in cur.fetchall()]
            # The revive, where it has to be true. The state moved, the
            # attempts were restored, and the error that killed it survived —
            # the SQL this replaces would have nulled it.
            cur.execute("select state, attempts, last_error from action"
                        " where kind = 'send' and channel = 'email'"
                        " order by updated_at desc limit 1")
            report["action_revived"] = dict(row) if (row := cur.fetchone()) else None
            cur.execute("select actor, detail from audit_log"
                        " where action = 'outbox.revived'")
            report["revive_audited"] = [dict(r) for r in cur.fetchall()]
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

        # Every stage the document targets, on a tenant that has sent nothing.
        # A commitment exists from the day the plan is signed, and a target
        # that appears only once it is being missed is one nobody planned
        # against. All three, because a stage measured in the runtime and
        # missing from the screen is a measurement nobody reads.
        sla_panel = report.get("sla_panel") or ""
        stages = [f"Tier A {stage}" for stage in ("available", "proposed", "executed")]
        if (any(row not in sla_panel for row in stages)
                or "no observations yet" not in sla_panel):
            print("::error::the signals view does not carry the time-to-touch "
                  "verdict docs/06 publishes:", json.dumps(sla_panel)[:400],
                  file=sys.stderr)
            return 1

        # Live, no reload. The approve and the publish both re-rendered in
        # place: the window markers survived, and the rail's review count fell
        # while the operator watched — the exit criterion `docs/28` OX-6 set.
        if not report.get("approve_in_place") or not report.get("publish_in_place"):
            print("::error::an action still throws the page away:",
                  json.dumps({k: report.get(k) for k in ("approve_in_place", "publish_in_place")}),
                  file=sys.stderr)
            return 1
        rail = report.get("review_rail_before_after") or ["", ""]
        if rail[0] == rail[1]:
            print("::error::the review count in the rail did not move after an approval:",
                  json.dumps(rail), file=sys.stderr)
            return 1

        # Which copy works. One positive reply on one send is a count and not
        # a rate: the panel names the step, the send and the reason there is
        # no rate — the exit criterion `docs/28` OX-4 set, on the screen.
        copy_panel = report.get("copy_panel") or ""
        if ("WHICH COPY WORKS" not in copy_panel.upper()
                or not re.search(r"email_1\s+1 sent", copy_panel)
                or not re.search(r"no rate, 1 of \d+ needed", copy_panel)):
            print("::error::the programme detail does not say which copy works, "
                  "or shows a rate the floor refuses:",
                  json.dumps(copy_panel)[:600], file=sys.stderr)
            return 1

        # The palette reaches everything: the Outbox from the keyboard and a
        # contact by name, onto their timeline (docs/28, OX-8).
        if (report.get("palette_reached_outbox") != "outbox"
                or "Iker" not in (report.get("palette_reached_contact") or "")):
            print("::error::the palette does not reach a screen or a contact:",
                  json.dumps({k: report.get(k) for k in
                              ("palette_reached_outbox", "palette_reached_contact")}),
                  file=sys.stderr)
            return 1

        # Which signals earn their keep. One catalogue signal was seeded with
        # a signal, an enrolment, a sent touch and a conversion inside the
        # window; the other eight never fired. Every one of the nine has a
        # row, the earner leads, and the panel reads its funnel back to the
        # conversion — the exit criterion `docs/28` OX-3 set, read off the
        # screen rather than off the endpoint.
        funnel_rows = report.get("funnel_rows") or []
        first = funnel_rows[0] if funnel_rows else ""
        if "hiring.role_opened" not in first or not re.search(r"Converted\s+1 inside \d+ days",
                                                             sla_panel):
            print("::error::the signals view does not show which signal earned its "
                  "keep:", json.dumps(funnel_rows)[:300], json.dumps(sla_panel)[:400],
                  file=sys.stderr)
            return 1
        if int(report.get("funnel_count") or 0) < 9:
            print("::error::the funnel hides the signals that never fired:",
                  report.get("funnel_count"), "rows", file=sys.stderr)
            return 1

        # The worklist has to name the work and what it costs, not just count
        # it. A number with no reason beside it is the nine screens again.
        today = (report.get("today_list") or "") + (report.get("today_detail") or "")
        if "never activated" not in today:
            print("::error::the console does not open on the work: a programme "
                  "published and never activated is not on Today:",
                  json.dumps(today)[:400], file=sys.stderr)
            return 1
        # Ranked by what ignoring each item costs. Asserted as the pairwise
        # claims the ranking actually makes, rather than by pinning the first
        # row: a check that pins position zero breaks whenever the seed grows
        # a higher-cost item, which is the check being wrong rather than the
        # product. Read off the rendered text, never from `zolts.attention` —
        # comparing the order against the table that produced it would pass on
        # any table.
        order = report.get("today_order") or []

        def rank_of(phrase):
            for i, row in enumerate(order):
                if phrase in row:
                    return i
            return None

        ladder = [
            ("gave up", "an action nothing will ever deliver"),
            ("past their deadline", "a promise already broken"),
            ("Drafts waiting", "a clock running against a commitment"),
            ("never activated", "a programme that has not started"),
        ]
        seen = [(phrase, what, rank_of(phrase)) for phrase, what in ladder]
        missing = [phrase for phrase, _, at in seen if at is None]
        if missing:
            print("::error::Today is missing work it was seeded with:",
                  json.dumps(missing), json.dumps(order)[:300], file=sys.stderr)
            return 1
        for (above, above_is, i), (below, below_is, j) in zip(seen, seen[1:]):
            if i >= j:
                print(f"::error::Today is not ranked by what ignoring each item "
                      f"costs: {above_is} ({above!r}) has to outrank "
                      f"{below_is} ({below!r}):",
                      json.dumps(order)[:300], file=sys.stderr)
                return 1
        if "until somebody presses Activate" not in today:
            print("::error::Today lists the work and not what ignoring it costs:",
                  json.dumps(today)[:400], file=sys.stderr)
            return 1

        cost_panel = report.get("cost_panel") or ""
        if "Tokens per contact" not in cost_panel or "Target" not in cost_panel:
            print("::error::the spend view does not carry the cost per contact "
                  "docs/08 targets:", json.dumps(cost_panel)[:400], file=sys.stderr)
            return 1

        if not report.get("tasks_rendered"):
            print("::error::the human task queue rendered no row on a tenant that "
                  "has one waiting", file=sys.stderr)
            return 1
        detail_text = report.get("task_detail") or ""
        # "3 h late", not merely a red dot: the lateness is computed from the
        # deadline the step stamped, and a label that does not carry the amount
        # is a label somebody has to go and work out.
        if "late" not in detail_text or "call_revops" not in detail_text:
            print("::error::a task three hours past its deadline is not shown as "
                  "late, or has no step:", json.dumps(detail_text)[:300],
                  file=sys.stderr)
            return 1
        if "past due" not in (report.get("task_list") or ""):
            print("::error::the task row does not say it is past due:",
                  json.dumps(report.get("task_list"))[:300], file=sys.stderr)
            return 1
        if not (report.get("domain_stopped") or {}).get("paused"):
            print("the sending switch reported success over a domain that is "
                  "still sending", file=sys.stderr)
            return 1
        if not report.get("stop_audited"):
            print("a domain was stopped and nothing recorded who or why",
                  file=sys.stderr)
            return 1
        forecast = report.get("preview_before_activate") or ""
        if "forecast as of" not in forecast and "cannot be evaluated" not in forecast:
            print("::error::the programme has no forecast beside Activate:",
                  json.dumps(forecast)[:400], file=sys.stderr)
            return 1
        if "Would enrol now" in forecast and "Held out" not in forecast:
            print("::error::the forecast counts enrolments without saying who is held out:",
                  json.dumps(forecast)[:400], file=sys.stderr)
            return 1
        story = report.get("timeline_text") or ""
        for needle, what in (("b2b.legitimate_interest", "the rule key"),
                             ("f00dfeedcafe", "the pack digest"),
                             ("Lisbon office", "the dropped claim")):
            if needle not in story:
                print(f"::error::the timeline does not show {what} for a contact "
                      f"who received a gated send:", json.dumps(story)[:400],
                      file=sys.stderr)
                return 1
        wanted = {"enrollment.entered", "decision.allow", "proposal.drafted", "touch.sent"}
        if not wanted <= set(report.get("timeline_kinds") or []):
            print("::error::the timeline is missing kinds the seed wrote:",
                  json.dumps(report.get("timeline_kinds")), file=sys.stderr)
            return 1
        if "credits" not in (report.get("buy_offered") or ""):
            print("::error::the buy control does not say what it will spend:",
                  json.dumps(report.get("buy_offered")), file=sys.stderr)
            return 1
        if "legal basis" not in (report.get("basis_required") or "").lower():
            print("::error::the console bought a field without a legal basis, "
                  "which is recorded against every value and cannot be "
                  "reconstructed later:",
                  json.dumps(report.get("basis_required")), file=sys.stderr)
            return 1
        revived = report.get("action_revived") or {}
        if revived.get("state") != "pending":
            print("the outbox revive reported success over an action that is "
                  "still dead", file=sys.stderr)
            return 1
        if not revived.get("last_error"):
            print("the revive erased what killed the action, which is the "
                  "defect it exists to replace", file=sys.stderr)
            return 1
        if not report.get("revive_audited"):
            print("an action was put back on the wire and nothing recorded "
                  "who or why", file=sys.stderr)
            return 1
        if not report.get("task_completed"):
            print("::error::Mark done reported success and no task was completed "
                  "in the database", file=sys.stderr)
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
        # Bulk, with reasons: the batch took the two drafts the single approve
        # left, after refusing a thin reason by name (docs/28, OX-7).
        waiting = [p for p in report["proposals_in_database"]
                   if p["state"] in ("draft", "needs_human")]
        if (not report.get("batch_approved") or waiting
                or "selected" not in (report.get("batch_offered") or "").lower()
                or "reason" not in (report.get("thin_batch_reason_refused") or "").lower()):
            print("::error::the batch did not take the queue, or took it without a reason:",
                  json.dumps({k: report.get(k) for k in
                              ("batch_offered", "thin_batch_reason_refused", "batch_approved")}),
                  len(waiting), "still waiting", file=sys.stderr)
            return 1
        # The phone approves (docs/28, OX-9): the decision came to the thumb,
        # the buttons were sized for one, nothing was clipped or drawn off the
        # edge, and the draft and the task moved in the database.
        phone_keys = ("phone_panel_in_view", "phone_panel_clipped", "phone_approve_height",
                      "phone_done_height", "phone_approved", "phone_task_done",
                      "phone_horizontal_overflow")
        if (not report.get("phone_panel_in_view") or report.get("phone_panel_clipped")
                or (report.get("phone_approve_height") or 0) < 40
                or (report.get("phone_done_height") or 0) < 40
                or report.get("phone_approved") not in ("approved", "dispatched")
                or not report.get("phone_task_done")
                or report.get("phone_horizontal_overflow")):
            print("::error::the phone did not approve:",
                  json.dumps({k: report.get(k) for k in phone_keys}), file=sys.stderr)
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
