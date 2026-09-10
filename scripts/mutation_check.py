#!/usr/bin/env python3
"""Break each guard on purpose, and require a test to notice.

Every guard in this repository was verified once, by hand, at the moment it was
written: change the line, watch a test fail, change it back. That verification
is not repeatable, and a guard erodes silently — somebody simplifies it, the
test still passes because it asserts the wrong thing, and the protection is
gone with a green build. It has happened here twice (D-17, D-26), both times to
a test that had been watched to fail once.

**This is not a mutation coverage measurement.** It does not generate mutants,
it does not sample, and it says nothing about the code it does not name. It
re-proves a curated list of guards whose failure would be expensive, which is
a smaller claim and an honest one. `docs/24` VER-1 keeps the larger item open.

Two rules make it worth running:

* A mutation whose target string is no longer in the file is an **error**, not
  a skip. Code moving under a mutation is exactly when the check stops being
  applied, and skipping quietly is how a suite of guards becomes decoration.
* A mutation whose guard could not run is the same error wearing a different
  coat. Eight of these guards live in `requires_db` tests, and a skip exits
  zero — so run without a database this script reported eight survivors on
  code that was never wrong (D-71). It now refuses to report on a target whose
  tests need a database it has not got, and never counts an unrun test as a
  guard that failed to bite. Note that a *file-level* pass is not enough
  evidence either: `tests/test_metrics.py` holds five tests that need no
  database and five that do, so five passes beside five skips looked exactly
  like a guard surviving.
* It refuses to run against a dirty working tree. It edits source files and
  restores them; doing that on top of uncommitted work risks losing it.

    python3 scripts/mutation_check.py            # all of them
    python3 scripts/mutation_check.py audience   # the ones whose id matches
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Mutation:
    id: str
    claim: str          # the property, in the words the product uses for it
    path: str
    find: str
    replace: str
    tests: str          # what must fail once the guard is broken


MUTATIONS = (
    Mutation(
        id="admission-at-the-choke-point",
        claim="a program is admitted where it is stored, not in one of five callers",
        path="runtime/repo/programs.py",
        find="    admission.check(spec, key, _inherited_policy(cur, tenant_id))\n",
        replace="",
        tests="tests/test_admission.py"),
    Mutation(
        id="a-version-is-written-once",
        claim="republishing a live version with different content is refused",
        path="runtime/repo/programs.py",
        find='"   where program.spec_hash = excluded.spec_hash"',
        replace='""',
        tests="tests/test_product_invariants.py"),
    Mutation(
        id="a-decision-carries-a-reason",
        claim="every policy decision records why, not only allow or deny",
        path="runtime/repo/ledger.py",
        find='    if not (rationale or "").strip():',
        replace="    if False:",
        tests="tests/test_product_invariants.py"),
    Mutation(
        id="deals-must-be-answerable",
        claim="an audience that excludes open deals refuses until a CRM delivers some",
        path="runtime/engine/audience.py",
        find="    if relies_on_deals(sql) and not deals_are_answerable(cur):",
        replace="    if False:",
        tests="tests/test_opportunity.py"),
    Mutation(
        id="a-previous-key-still-opens",
        claim="a credential sealed under the old key opens during a rotation",
        path="runtime/crypto.py",
        find="    for candidate in (ring.primary, *ring.previous):",
        replace="    for candidate in (ring.primary,):",
        tests="tests/test_key_rotation.py"),
    Mutation(
        id="liveness-can-fail",
        claim="a failing signal answers 503, because a monitor reads the status code",
        path="runtime/api/app.py",
        find='        if not body["draining"]:\n            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE\n',
        replace="",
        tests="tests/test_hardening.py"),
    Mutation(
        id="only-priced-fields-are-bought",
        claim="a program may only buy enrichment fields the price list carries",
        path="runtime/engine/enrich_step.py",
        find='BUYABLE = ("email", "phone", "firmographics")',
        replace='BUYABLE = ("email", "phone", "firmographics", "tech_stack")',
        tests="tests/test_enrich_step.py"),
    Mutation(
        id="an-unreadable-payload-is-not-an-outage",
        claim="a payload that cannot answer the trigger is a non-match, not a 500",
        path="runtime/engine/triggers.py",
        find="    except TypeError as exc:",
        replace="    except NotImplementedError as exc:",
        tests="tests/test_signal_payloads.py"),
    Mutation(
        id="the-stricter-quiet-window-wins",
        claim="a policy override never buys a program more sending hours than the pack",
        path="zolts/policy.py",
        find="        if _quiet_span(window) > _quiet_span(rule.quiet_hours):",
        replace="        if True:",
        tests="tests/test_policy_overrides.py"),
    Mutation(
        id="a-tick-without-the-cron-secret-is-refused",
        claim="the scheduled routes run nothing for a caller without the cron's bearer",
        path="runtime/serverless.py",
        find='    if not hmac.compare_digest(presented, f"Bearer {secret}".encode()):',
        replace="    if False:",
        tests="tests/test_serverless.py"),
    Mutation(
        id="a-dead-worker-is-not-a-draining-deployment",
        claim="liveness fails when nothing has ticked, however empty the outbox is",
        path="runtime/liveness.py",
        find="    if last is None:\n        live.add(\"worker ticking\", False,",
        replace="    if last is None:\n        live.add(\"worker ticking\", True,",
        tests="tests/test_hardening.py"),
    Mutation(
        id="an-unresolvable-comparison-has-no-verdict",
        claim="a report declares no effect while either arm holds fewer than five conversions",
        path="zolts/report.py",
        find="        if not self.resolvable:\n            return NOT_RESOLVABLE\n",
        replace="        if False:\n            return NOT_RESOLVABLE\n",
        tests="tests/test_incrementality_report.py"),
    Mutation(
        id="a-percentage-score-is-not-certainty",
        claim="a provider that scores out of a hundred is not reported as certain",
        path="runtime/connectors/declarative_provider.py",
        find="float(raw) / _scale_of(lookup.field,\n                                                                         declared)",
        replace="float(raw)",
        tests="tests/test_provider_documents.py"),
    Mutation(
        id="a-conversion-outside-the-window-does-not-count",
        claim="a programme's metric stops counting when its window closes",
        path="runtime/api/console.py",
        find='        "   and o.occurred_at < e.entered_at + make_interval(days => %s)"\n'
             '        " group by e.variant",\n'
             '        (program_id, list(metric.events), metric.window_days))',
        replace='        " group by e.variant",\n'
                '        (program_id, list(metric.events)))',
        tests="tests/test_metrics.py"),
    Mutation(
        id="the-gate-decides-under-the-published-pack",
        claim="a send is judged by the pack that was published, not the one in this release",
        path="runtime/engine/gate.py",
        find="pack=policy_packs.rules_of(pack_row),",
        replace="pack=None,",
        tests="tests/test_policy_packs.py"),
    Mutation(
        id="the-accent-never-enters-a-data-region",
        claim="inside a chart, table or figure colour means a measurement, and the "
              "brand accent is barred from those regions (ADR-045)",
        path="design/console.html",
        find=".kpi .v.live{color:var(--live)}",
        replace=".kpi .v.live{color:var(--accent)}",
        tests="tests/test_brand.py"),
    Mutation(
        id="the-lift-is-rendered-in-the-panels-precision",
        claim="a frozen report's lift reaches the screen in percentage points to "
              "two decimals, the unit every other figure in the panel is in (D-58)",
        path="runtime/api/console.py",
        find='else round(primary["lift"] * 100, 2)),',
        replace='else round(primary["lift"] * 100, 3)),',
        tests="tests/test_survivors.py"),
    Mutation(
        id="a-baseline-date-is-exactly-ten-characters",
        claim="the baseline window a partner signs a letter against holds ISO dates "
              "and refuses one character more (D-58)",
        path="runtime/api/schemas.py",
        find="    window_start: str = Field(min_length=10, max_length=10)\n",
        replace="    window_start: str = Field(min_length=10, max_length=11)\n",
        tests="tests/test_survivors.py"),
    Mutation(
        id="every-documented-threshold-is-a-rule",
        claim="the reply-rate alarm docs/09 calls non-negotiable fires at the 2% "
              "the document names, and not only at the 1% below it (D-87)",
        path="zolts/deliverability.py",
        find="""    ("reply.alarm",        "reply_rate",       0.02,   Health.ALARM,
     "reply rate below 2%"),
""",
        replace="",
        tests="tests/test_operating_thresholds.py"),
    Mutation(
        id="an-advisory-alarm-costs-no-capacity",
        claim="a weak reply rate is reported and does not halve a mailbox, and a "
              "collapsed one still does — the two bounds of one docs/09 row mean "
              "different things (D-87)",
        path="zolts/deliverability.py",
        find='ADVISORY = frozenset({"reply.alarm"})',
        replace="ADVISORY = frozenset()",
        tests="tests/test_operating_thresholds.py"),
    Mutation(
        id="a-compliance-claim-cannot-outrun-the-code",
        claim="a GDPR obligation docs/11 marks Not built stays unimplemented, "
              "so the day one ships the page is corrected rather than quietly "
              "overtaken (D-89)",
        path="docs/11-compliance-and-governance.md",
        find="| Retention | **Not built** |",
        replace="| Retention | **Built** |",
        tests="tests/test_compliance_claims.py"),
    Mutation(
        id="the-disclosure-check-is-off-and-the-page-says-so",
        claim="requires_ai_disclosure defaults off at every call site, which is "
              "what docs/11 tells a reader, so switching it on turns the page "
              "red instead of leaving it wrong (D-90)",
        path="runtime/engine/generate.py",
        find="        requires_ai_disclosure: bool = False) -> Generated:",
        replace="        requires_ai_disclosure: bool = True) -> Generated:",
        tests="tests/test_compliance_claims.py"),
    Mutation(
        id="the-disclosure-marker-is-required-where-the-pack-says",
        claim="the shipped pack requires an AI-disclosure marker in the EU "
              "jurisdictions it covers, and a message without one reaches a "
              "person instead of a prospect (D-90, decision 55)",
        path="zolts/policy.py",
        find='        suppression_lists=("robinson_list_es",),\n        ai_disclosure=True,',
        replace='        suppression_lists=("robinson_list_es",),',
        tests="tests/test_ai_disclosure.py"),
    Mutation(
        id="a-newer-shipped-pack-takes-the-seat",
        claim="a release that changes the shipped pack changes what a running "
              "deployment decides under, rather than migrating and leaving the "
              "old rules in place (D-91)",
        path="runtime/policy_packs.py",
        find="    governs = active is None or (\n"
             "        active[\"published_by\"] == SHIPPED and active[\"digest\"] != digest)",
        replace="    governs = active is None",
        tests="tests/test_policy_packs.py"),
    Mutation(
        id="the-frequency-cap-counts-a-person",
        claim="the touches-per-week cap counts a person across every programme "
              "and channel, not an account's enrolment in one of them (D-92)",
        path="runtime/engine/gate.py",
        find='    touches_week = ledger.touches_this_week(cur, str(person["id"]))',
        replace="    touches_week = 0",
        tests="tests/test_frequency_cap.py"),
    Mutation(
        id="a-sent-touch-names-its-person",
        claim="the worker records who a sent touch reached, without which the "
              "cap counts nothing and the only symptom is a fourth message "
              "in a week (D-92)",
        path="runtime/engine/worker.py",
        find='            mailbox_id=mailbox_id, person_id=str(person["id"]) if person else None)',
        replace="            mailbox_id=mailbox_id)",
        tests="tests/test_frequency_cap.py"),
    Mutation(
        id="the-agent-layer-page-cannot-outrun-the-agents",
        claim="an agent docs/08 marks Not built stays unbuilt, so the day one "
              "ships the page is corrected rather than quietly overtaken (D-93)",
        path="docs/08-ai-agent-layer.md",
        find="| **Strategist** | **Not built** |",
        replace="| **Strategist** | **Built** |",
        tests="tests/test_agent_layer_claims.py"),
    Mutation(
        id="a-programme-may-not-loosen-its-archetype",
        claim="the overlay resolver runs at the publish choke point against the "
              "tenant's blueprint, so a programme cannot widen what its archetype "
              "permits — it had no caller at all until D-94",
        path="runtime/repo/programs.py",
        find="    admission.check(spec, key, _inherited_policy(cur, tenant_id))",
        replace="    admission.check(spec, key)",
        tests="tests/test_inherited_policy.py"),
    Mutation(
        id="the-time-to-touch-target-is-the-strict-bound-docs-06-publishes",
        claim="every target in docs/06's time-to-touch table is read as the "
              "strict `p95 <` the document writes, so a tier sitting exactly on "
              "its contractual bound misses it rather than passing (D-97)",
        path="zolts/latency.py",
        find="    return Verdict.MEETS if p95_minutes < target else Verdict.MISSES",
        replace="    return Verdict.MEETS if p95_minutes <= target else Verdict.MISSES",
        tests="tests/test_time_to_touch_sla.py"),
    Mutation(
        id="an-unmeasured-sla-stage-is-never-reported-as-met",
        claim="every time-to-touch stage docs/06 targets has a probe, and the "
              "page's status table has to agree in both directions — a stage "
              "losing its probe fails rather than reading as met (D-97, SIG-1)",
        path="zolts/latency.py",
        find="MEASURED: frozenset[Stage] = frozenset({Stage.AVAILABLE, Stage.PROPOSED,",
        replace="MEASURED: frozenset[Stage] = frozenset({Stage.PROPOSED,",
        tests="tests/test_time_to_touch_sla.py"),
    Mutation(
        id="the-push-path-stamps-when-the-payload-arrived",
        claim="`POST /v1/signals` records when the request reached this runtime, "
              "without which docs/06's first SLA column has a column and no data "
              "(SIG-1)",
        path="runtime/api/app.py",
        find="                result = enroll.ingest(cur, principal.tenant_id, received_at=received_at,",
        replace="                result = enroll.ingest(cur, principal.tenant_id,",
        tests="tests/test_time_to_touch_sla.py"),
    Mutation(
        id="the-watching-pass-stamps-when-the-batch-arrived",
        claim="the detection loop records when the source handed the batch back, "
              "so the arrival stage measures this runtime's work rather than the "
              "source's (SIG-1)",
        path="runtime/watch.py",
        find="                                received_at=received_at)",
        replace="                                received_at=None)",
        tests="tests/test_time_to_touch_sla.py"),
    Mutation(
        id="the-document-says-the-scheme-the-stylesheet-paints",
        claim="the console document reads its colour scheme off the surface's own "
              "canvas token, so it cannot go back to telling the browser the "
              "opposite of what the page paints (D-99)",
        path="runtime/surface.py",
        find='    return "light" if 0.2126 * red + 0.7152 * green + 0.0722 * blue > 0.5 else "dark"',
        replace='    return "dark"',
        tests="tests/test_deploy_drift.py"),
    Mutation(
        id="something-compares-production-with-main",
        claim="the static build stamps the page it builds, without which the drift "
              "check cannot tell a stale production from a current one — thirty-nine "
              "green runs over a build weeks old (D-100)",
        path="scripts/build_site.py",
        find="    rendered = document(inject(source, fixture), build=build_id(source),",
        replace="    rendered = document(inject(source, fixture),",
        tests="tests/test_deploy_drift.py"),
    Mutation(
        id="a-token-target-counts-only-token-spend",
        claim="docs/08's cost per contact counts the model kinds only, so a data "
              "supplier's invoice cannot reach a target about tokens (AGENT-4)",
        path="zolts/agentcost.py",
        find='MODEL_KINDS: frozenset[str] = frozenset({"agent.generate", "agent.dossier"})',
        replace='MODEL_KINDS: frozenset[str] = frozenset({"agent.generate", "agent.dossier", "enrich.email"})',
        tests="tests/test_agent_cost_per_contact.py"),
    Mutation(
        id="the-cost-denominator-is-a-person-not-a-touch",
        claim="a contact reached three times is one contact touched, so the "
              "margin number cannot be divided down by sending more (AGENT-4)",
        path="runtime/metering.py",
        find='        "select count(distinct t.person_id) as contacts,"',
        replace='        "select count(t.person_id) as contacts,"',
        tests="tests/test_agent_cost_per_contact.py"),
    Mutation(
        id="the-human-task-queue-has-a-screen-that-reaches-it",
        claim="the rail entry for the human task queue reaches its renderer, "
              "without which the queue is a link to nothing and the work nobody "
              "can see is work nobody does",
        path="design/console.html",
        find='  if (state.view === "tasks"){ renderTasks(); return; }',
        replace='  if (state.view === "tasks"){ return; }',
        tests="tests/test_task_queue_view.py"),
    Mutation(
        id="the-worklist-is-ranked-by-cost-not-by-recency",
        claim="Today ranks what needs a person by what ignoring it costs, so an "
              "irreversible sending alarm cannot be pushed below a draft "
              "waiting for approval by arriving earlier",
        path="zolts/attention.py",
        find='    return sorted(items, key=lambda item: RANK[kind(item["kind"]).key])',
        replace="    return list(items)",
        tests="tests/test_attention.py"),
)

def _dirty() -> bool:
    """Modified tracked files only.

    An untracked new file is not at risk from writing and restoring a tracked
    one, and refusing to run because of it would make this script unusable in
    the pull request that adds it.
    """
    out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                         capture_output=True, text=True).stdout.splitlines()
    return any(not line.startswith("??") for line in out)


PASSED, FAILED, NOTHING_RAN = "passed", "failed", "nothing ran"
_OUTCOME = re.compile(r"(\d+) (passed|failed|error)")


def _needs_a_database(target: str) -> bool:
    """Whether this target holds tests marked `db`.

    Asked by collecting, not by reading the file: the marker travels through
    `requires_db` and through `pytestmark`, and a grep for either would miss
    the third way somebody adds it next year.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", target, "--collect-only", "-q",
         "-m", "db", "-p", "no:cacheprovider", "-p", "no:randomly"],
        cwd=ROOT, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": "."})
    return bool(re.search(r"(\d+)/\d+ tests collected", result.stdout)
                or re.search(r"^\d+ tests? collected", result.stdout, re.M))


def _run_tests(target: str) -> str:
    """`passed`, `failed`, or `nothing ran`.

    Passing under a mutation is the failure this script looks for. A target
    whose tests all skipped is neither: nothing was checked, and reporting it
    as a survivor blames the code for the absence of a database. Read from
    pytest's own summary rather than its exit code, because a file of skips
    exits zero exactly like a file of passes.
    """
    env = {**os.environ, "PYTHONPATH": "."}
    env.setdefault("ZOLTS_SECRET_KEY", "mutation-check-secret")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-x", "-q", "-p", "no:cacheprovider",
         "-p", "no:randomly"],
        cwd=ROOT, capture_output=True, text=True, env=env)
    ran = sum(int(n) for n, _ in _OUTCOME.findall(result.stdout))
    if not ran:
        return NOTHING_RAN
    return PASSED if result.returncode == 0 else FAILED


def check(selection: str | None = None) -> int:
    if _dirty():
        print("::error::the working tree is dirty. This script edits source files and "
              "restores them; run it on a clean tree so a crash cannot lose work.",
              file=sys.stderr)
        return 2

    chosen = [m for m in MUTATIONS if selection is None or selection in m.id]
    if not chosen:
        print(f"::error::no mutation matches '{selection}'", file=sys.stderr)
        return 2

    # Establish the premise before reporting a verdict about the code. Eight of
    # these guards are held by tests that need Postgres, and without one the
    # script used to break the guard, watch the *other* tests in the file pass,
    # and call it a survivor (D-71). A partial run is not a smaller result; it
    # is a different one, so it is refused rather than reported.
    if not os.environ.get("ZOLTS_TEST_DATABASE_URL"):
        needs = [m for m in chosen if _needs_a_database(m.tests)]
        if needs:
            print("::error::ZOLTS_TEST_DATABASE_URL is not set, and these guards are held "
                  "by tests that need Postgres: " + ", ".join(m.id for m in needs)
                  + ". Breaking them and watching the rest of their file pass is not a "
                  "result about the code. Set it, or select mutations that do not need it.",
                  file=sys.stderr)
            return 2

    survived: list[Mutation] = []
    stale: list[Mutation] = []
    unchecked: list[Mutation] = []
    for mutation in chosen:
        path = ROOT / mutation.path
        original = path.read_text()
        if mutation.find not in original:
            # The code moved. Not a pass and not a skip: the guard this
            # mutation was written for is no longer where it was, so nothing
            # here is being checked.
            stale.append(mutation)
            print(f"STALE   {mutation.id}: the target is no longer in {mutation.path}")
            continue
        path.write_text(original.replace(mutation.find, mutation.replace, 1))
        try:
            outcome = _run_tests(mutation.tests)
        finally:
            path.write_text(original)
        if outcome == FAILED:
            print(f"BITES   {mutation.id}: {mutation.claim}")
        elif outcome == NOTHING_RAN:
            unchecked.append(mutation)
            print(f"UNCHECKED {mutation.id}: {mutation.claim}")
            print(f"         every test in {mutation.tests} skipped, so nothing was "
                  f"checked. Set ZOLTS_TEST_DATABASE_URL to run this one")
        else:
            survived.append(mutation)
            print(f"SURVIVED {mutation.id}: {mutation.claim}")
            print(f"         broke {mutation.path} and {mutation.tests} still passed")

    checked = len(chosen) - len(unchecked)
    bite = checked - len(survived) - len(stale)
    print(f"\n{bite}/{checked} guards bite"
          + (f", {len(unchecked)} of {len(chosen)} not checked" if unchecked else ""))
    if stale:
        print("::error::stale mutations (the code moved): "
              + ", ".join(m.id for m in stale), file=sys.stderr)
    if survived:
        print("::error::guards that did not bite: "
              + ", ".join(m.id for m in survived), file=sys.stderr)
    if unchecked:
        print("::error::guards nothing checked, because their tests all skipped: "
              + ", ".join(m.id for m in unchecked)
              + ". This is not a result about the code; set ZOLTS_TEST_DATABASE_URL",
              file=sys.stderr)
    return 1 if (survived or stale or unchecked) else 0


if __name__ == "__main__":
    raise SystemExit(check(sys.argv[1] if len(sys.argv) > 1 else None))
