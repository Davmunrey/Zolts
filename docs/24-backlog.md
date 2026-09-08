# 24 · Backlog

What is built, what is next, and what is blocked on a decision rather than on engineering.

**The single fact that orders everything below: the runtime is not deployed.** Every item in "Built" is verified against real infrastructure and reachable by nobody. The host is decided and the release path is written and tested (ADR-041); until the founder sets the secrets it names, nothing else on this list changes the company's position.

## Status at a glance

| | Count | Evidence |
|---|---|---|
| Epics delivered | 42 | `docs/22`, ADR-001 … ADR-043 |
| Tests | 1132 | 373 against a real Postgres; CI fails a run that skipped them |
| Priced actions executable | 8 of 8 | `docs/12` vs `zolts/billing.py`, checked by test |
| Console views | 8, no dead links | ADR-023 |
| Native CRMs | 3 | HubSpot, Pipedrive, Salesforce — all three read deals |
| Sending channels | **1** | Decision 27, closed: a LinkedIn step is a person's task until a partner asks |
| Production tenants | **0** | Blocked on B-1 |

## Now — the only thing that matters this week

| ID | Item | Why | Size | Blocked by |
|---|---|---|---|---|
| **B-1** | **Release the runtime to Vercel + Neon** | Everything built is unreachable. A product in production with one channel is worth more than a perfect one with no users | S — the path is written, executed in tests, and released by a workflow that migrates and preflights first (ADR-041) | **Founder**: the Pro plan; GitHub secrets `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, `ZOLTS_DATABASE_URL`, `ZOLTS_APP_DATABASE_URL`, `ZOLTS_SECRET_KEY`; the same three `ZOLTS_*` values plus `CRON_SECRET` and `ZOLTS_ENV=production` in the Vercel project. Never through chat, an issue, or a file. `docs/20` has the checklist |
| **B-2** | Set the `ZOLTS_URL` repository variable | From then on `smoke_deployed.py --strict` runs every hour from `deploy-vercel.yml` and a failed run notifies the owner — the difference between the process being up and the product answering, checked without anybody remembering to | XS | B-1 |
| **B-3** | First paying partner onboarded end to end | The only validation that matters. The path is `docs/26`: invitation → letter → baseline → CRM → sending → activate → first send, checked command by command; the three partners' names and countries are the founder's to give | M | B-1, **Founder** (names) |

**Acceptance for B-1:** `preflight` passes in production mode against the real database; `/health` reports every migration applied; `worker ticking` is ok, which on this host means the cron fires; one program activates and one action reaches a provider.

## Next — blocked on a founder decision, not on engineering

| ID | Item | Default if undecided | Decision |
|---|---|---|---|
| **B-5** | Repository visibility | Open. `docs/12` (pricing, COGS, margins) and `docs/15` (risk matrix) are world-readable, and `zolts.vercel.app` serves the demo with no authentication | **Founder**, `docs/23` |
| **B-6** | Data residency per tenant | Single region: `fra1` in `vercel.json`, beside a Neon database the founder creates in `eu-central-1`; `mad` in `fly.toml` | `docs/18` |

## Then — engineering, unblocked, ordered by what being wrong costs

### Security and operations

| ID | Item | Consequence of not doing it | Size |
|---|---|---|---|
| **SEC-2** | Backup schedule and a restore drill on a real managed Postgres | The restore script is now executed in tests (ADR-033) against a local cluster. It has never run against Neon | S |
| **OPS-3** | Route the 503 somewhere that wakes a person | Narrower than it was: once `ZOLTS_URL` is set, the hourly strict smoke in `deploy-vercel.yml` fails and notifies the repository owner when any signal fails, including a cron that stopped ticking. What remains is a monitor with a pager rather than an email | XS |

### Verification

| ID | Item | Why | Size |
|---|---|---|---|
| **VER-1** | Measure mutation coverage across the runtime | Still open, and narrower than it was. `scripts/mutation_check.py` re-proves fourteen named guards on every push — that is regression-proofing a curated list, not a coverage measurement, and it says nothing about the code it does not name. What remains is the real number: generate mutants across `runtime/` and `zolts/` and report the fraction that survives. It needs a tool and a CI budget neither of which exists yet | M |

### Product

| ID | Item | Why | Size |
|---|---|---|---|
| **DATA-1** | A second supplier behind `enrich.email` | `docs/07` makes two providers per critical field the condition for GA and `docs/15` ranks single-provider dependence high. One is registered (decision 38); the optimiser cannot reorder around a supplier that is the only one | S — a second document, once a founder picks the supplier |
| **P-1** | Zero-copy over a customer warehouse | ADR-003 stated it in the present tense with no implementation, now corrected (decision 29). It returns as a real item when a partner's DPO or data volume makes the copy the objection | L |
| **P-2** | Program editing beyond the eleven dials | The console emits DSL for money-and-risk parameters (ADR-029). Audience SQL and play copy deliberately stay in a pull request (decision 30). Revisit when an operator asks for a twelfth | M |
| **P-3** | Brand voice classifier trained on the tenant's own material | Today the copywriter is guarded by evals and provenance, not by a model of the customer's voice | L |
| **P-4** | Console for multiple tenants in one view | Every screen is single-tenant. An agency partner needs a switcher | M |

## Built

Delivered and verified against real infrastructure. Grouped by what a buyer would ask about.

| Area | What exists | Verified by |
|---|---|---|
| **Isolation** | RLS forced on every tenant-scoped table; unscoped reads raise; two `security definer` functions the whole privileged surface | `test_runtime_isolation.py`, preflight, ADR-008 |
| **Execution** | Postgres outbox with leases, `for update skip locked`, at-least-once at the runtime and exactly-once at the provider via idempotency keys | ADR-005, ADR-007 |
| **Policy** | Jurisdiction packs; every external action records an allow or deny **with a reason**; suppression shared by provider events and text opt-outs | ADR-016, ADR-031, migration 018 |
| **Agents** | Propose only, structurally barred from the machinery that acts on a prospect; spend-guarded; a verdict must quote | ADR-004, ADR-012, ADR-031 |
| **Measurement** | Holdout mandatory or waived in writing; MDE reported beside lift; the unread share of conversions disclosed on the same screen | ADR-016, `docs/10` |
| **Billing** | Eight priced actions all executable and metered; graduated overage; a closed period keeps its terms; an unpriced action raises | ADR-017, ADR-024, ADR-025, ADR-027 |
| **CRM** | Three native connectors on one contract; the long tail connected by a mapping document, not code | ADR-006, ADR-013, ADR-014, ADR-018 |
| **Deals** | Accounts already in a live sales conversation are excluded from outbound, and a CRM that cannot say which asks the program to refuse rather than assuming none | ADR-038, `test_opportunity.py` |
| **Deliverability** | Per-domain capacity, warmup, circuit breakers, mailbox fleet with reputation kept where it was earned | ADR-020 |
| **Enrichment** | Waterfall bought in the declared order with measured hit rates; a miss is absorbed, not billed | ADR-021 |
| **Signals** | Nine definitions; the runtime goes and looks rather than waiting to be told; priced per account-day | ADR-022 |
| **Console** | Eight views, no dead links, operable on a phone, and it emits DSL | ADR-023, ADR-028, ADR-029 |
| **Deploy** | Image built, migrated, seeded, preflighted and booted in CI — on the same two commands `fly.toml` runs. On Vercel the same app is one function and the same worker a cron; production is released only by the job that migrates and preflights first, and the function refuses to serve unconfigured with a 503 that says which variable is missing. A probe deployment to the real project built the function with every runtime dependency and proved the rewrite delivers the original path; the minute cron is refused below Pro, as designed | ADR-032, ADR-041, `test_serverless.py` |
| **Restore** | `restore.sh` executed end to end against a real dump, with the failure it guards against reproduced | ADR-033 |
| **Key rotation** | The key sealing every credential can be replaced with no downtime: both keys open during the window, `rotate-key` re-seals resumably, and preflight reports what is still on the old one | ADR-039, `test_key_rotation.py` |
| **Operations** | The liveness endpoint answers 503 when a signal fails, so a monitor pointed at it fires; a worker that stops ticking is reported within five minutes, work or no work; `docs/25` says what to do about each signal, and every command on that page was executed while it was written | D-36, D-39, `docs/25`, `test_runbook.py` |
| **Deployed smoke** | Hourly and strict against the production URL once it is named: every liveness signal, every migration on disk applied, `/api/tick` locked to a stranger. The script itself is executed in tests against live servers in both shapes a deployment takes — the served API and the Vercel entry point — including the release before the secrets exist and a release whose schema is behind its code | `deploy-vercel.yml`, `scripts/smoke_deployed.py`, `test_smoke_deployed.py` |
| **Guards that bite** | Fourteen guards are broken on purpose on every push and a test has to notice. A mutation whose target moved is an error, not a skip | `scripts/mutation_check.py`, `test_mutation_targets.py` |
| **The console on a phone** | Nothing may be drawn on top of text: the check measures where the glyphs start rather than where the box does, which is what made three collisions invisible to every clipping and overflow check | D-38, `browser_console.py` |
| **Admission** | A program is checked where it is stored, not where it arrives: holdout, audience, enrichment pricing and policy overrides run inside `publish`, so signup and the CLI get the same answer as the API | ADR-037, `test_admission.py` |
| **Baseline** | What a tenant cost and produced before Zolts, frozen once at onboarding with a digest the pilot letter quotes; activation without one is noted where an operator looks | ADR-042, `test_baseline.py`, `docs/26` |
| **Model provider disclosure** | What each agent sends and never sends, that the agent layer is off by default, and that the endpoint is a per-deployment control — with the per-tenant promise two documents made and the code did not keep corrected | `docs/11`, D-42, `test_model_provider_disclosure.py` |
| **Incrementality report** | What a program did against its holdout, composed from what the runtime already records and frozen at every period close: arms, lift beside the detectable effect, the unread share, decisions, credits by kind, pipeline on opportunities only and only when significant, the baseline's digest quoted. Written once, the document stored verbatim, a verdict that is never *met* | ADR-043, `test_incrementality_report.py`, `smoke_runtime.py` |
| **A named data supplier** | `enrich.email` is priced, planned by the optimiser and now buyable: Hunter ships as a provider document a tenant registers against their own key, no connector written. A provider's confidence is reported on the scale it was given in, and a registration whose key and document disagree is refused before it is stored | decision 38, D-47, D-48, `test_provider_documents.py` |
| **The CFO's half of the console** | The frozen report is read on the programme it belongs to: verdict, lift beside the detectable effect, digest, credits, and the baseline it quotes. A tenant with none is told when one appears rather than shown an empty panel | ADR-043, decision 39, `browser_console.py` |
| **The metric a programme declares** | What `primary_metric` names is what the measurement counts, inside the window the name carries. A metric the runtime cannot count is refused where the programme is stored; a value metric's amount is reported and never tested | decision 40, D-51, `test_metrics.py` |

## How an item earns its place

The register in `docs/22` is the evidence for this ordering. Thirty-one of fifty-one defects were a specification with no caller. Reading found nineteen of them and every one of those was a *document* read against the code — a price nothing charges, an ADR about a connector that does not exist. Not one defect in the register was found by reviewing code for a wrong line.

So an item is only "done" when something runs it and that run can fail. A specification, a document, a column, a button and a process table have each been the whole of a feature in this repository, and each was found by an execution nobody had performed.
