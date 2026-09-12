# 24 · Backlog

What is built, what is next, and what is blocked on a decision rather than on engineering.

**The single fact that orders everything below: the runtime is not deployed.** Every item in "Built" is verified against real infrastructure and reachable by nobody. The host is decided and the release path is written and tested (ADR-041); until the founder sets the secrets it names, nothing else on this list changes the company's position.

## Status at a glance

| | Count | Evidence |
|---|---|---|
| Epics delivered | 43 | `docs/22`, ADR-001 … ADR-044 |
| Tests | 1928 | 564 against a real Postgres; CI fails a run that skipped them |
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

## Product depth — the road to enviable

Thirteen rows in `docs/28`, three levels, each with a binary exit criterion. Built in leverage order: OX-1 (why this person), OX-2 (what a programme would do today), then the day, then the polish. Each ships as its own pull request against the same bar as everything above.

## Then — engineering, unblocked, ordered by what being wrong costs

### Security and operations

| ID | Item | Consequence of not doing it | Size |
|---|---|---|---|
| **SEC-2** | Backup schedule and a restore drill on a real managed Postgres | The restore script is now executed in tests (ADR-033) against a local cluster. It has never run against Neon | S |
| **OPS-3** | Route the 503 somewhere that wakes a person | Narrower than it was: once `ZOLTS_URL` is set, the hourly strict smoke in `deploy-vercel.yml` fails and notifies the repository owner when any signal fails, including a cron that stopped ticking. What remains is a monitor with a pager rather than an email | XS |

### Verification

| ID | Item | Why | Size |
|---|---|---|---|
| **VER-1** | Mutation coverage across the runtime | **Done, and measured.** `zolts/` catches 39 of 40 sampled mutants — 97.5%, 95% CI 87-100%, from 512 possible. `runtime/` catches **27 of 30 — 90%, 95% CI 74-97%, from 1,473 possible** on a fresh draw at seed `20260908`, against 22 of 30 on the earlier sample the guards were written from. The improvement is **not statistically significant** at thirty mutants (two-proportion `z` 1.67, *p* 0.095) and `docs/22` says so beside the number. Each mutant costs a full Postgres suite run. Every real survivor from both samples is guarded and every equivalent one annotated where it lives, each verified by re-applying its own mutation. CI samples eight on every push to keep the sampler honest, and the measurement now runs against its own database so a suite and a mutation pass no longer contend | S |
| **VER-4** | The documents no test reads | **Done, and triaged rather than left open.** Fourteen of the twenty-eight files under `docs/` were named by no test and no script, so every number in them could drift from the product with nothing noticing. Six were audited and **eight defects came out of them**: `docs/09` (D-87), `docs/03` (D-88), `docs/11` (D-89 and D-90, and fixing D-90 exposed D-91), `docs/06` (D-92), `docs/08` (D-93) and `docs/05` (D-94). Seven of the eight were a documented capability with no implementation, and two — the frequency cap and the overlay resolver — were controls a customer was relying on. Each audited document now carries a status per claim and a test that fails in **both** directions (ADR-053, ADR-054, ADR-057). The remaining ten are triaged below rather than forced: a test that parses a market thesis asserts prose, and writing one would be the same defect this item exists to catch | — |
| | `docs/07`, `docs/19` | **No new check needed.** Both already read as corrected documents that cite their own defect numbers and name the code that implements them. Re-auditing found nothing |
| | `docs/00`, `docs/01`, `docs/13`, `docs/15`, `docs/16`, `docs/17` | **No checkable claims.** Executive summary, market thesis, roadmap, risk register, team plan and buyer sequencing. They describe intent, market and sequence, not runtime behaviour. `docs/13` and `docs/15` carry dates and sizes that a test could parse and could not falsify |
| | `docs/21` | **Covered indirectly.** The demo script is executed by `scripts/smoke_runtime.py` and `scripts/seed_demo.py` on every push; a document describing what they do is checked by them failing |
| | `docs/05` | Audited. D-94 |

### Compliance

Six rows of `docs/11`'s GDPR table had no implementation (D-89, ADR-054). Each is sized here and each is guarded: the day one ships, `tests/test_compliance_claims.py` fails until the document is corrected. Five need a founder or counsel before an engineer.

| ID | Item | Why | Size |
|---|---|---|---|
| **COMP-1** | A retention job that runs | `docs/03` publishes four retention classes and nothing purges a row on age. The window is settled (H10, twelve months for non-converted PII); what a deletion cascades to is decision 54, and getting it wrong destroys the evidence behind a closed invoice and a signed report | M — after decision 54 |
| **COMP-2** | Subject access and erasure | No code resolves a request through the identity graph, exports, erases, propagates to a sub-processor or issues a certificate. It is the row a DPO asks about first and it depends on COMP-1's cascade and COMP-3's register | L |
| **COMP-3** | A sub-processor register | Provider, DPA, region and status, with an alert when a new one is added. Blocked on the founder: the list is a commercial fact, not an engineering one | S — once the list exists |
| **COMP-4** | A privacy notice in the first contact | Nothing injects one. The copywriter already refuses a draft missing a required marker (`evals.compliance_checks`), so the mechanism is the same one COMP-6 and decision 55 use — what is missing is the text and the rule that selects it | S — once counsel supplies the text |
| **COMP-5** | A stored legitimate-interest assessment | `legitimate_interest` is a basis a programme declares; no assessment is captured, stored or versioned. A guided template is a product surface, and the surface needs the identity model COMP-7 covers to record who signed it | M |
| **COMP-6** | Records of processing generated from configuration | The claim was that a RoPA writes itself from live configuration. Nothing does. The inputs exist — programmes, channels, bases, providers, regions — so this is assembly rather than new state | M |
| **COMP-7** | An identity model: users, roles, sign-off | There is no `user`, `seat` or `role` table (D-82). It gates the RBAC `docs/11` describes, the DPO veto, MFA on day one, the per-rep routing capacity a programme may declare, and `approved_by` meaning a person rather than `key:<uuid>`. Decision 50 is the fork | L |

### Agent layer

`docs/08` described six agents and a model architecture in the present tense; three agents and four mechanisms did not exist (D-93, ADR-054's mechanism applied a third time). Each is sized here and each is guarded: the day one ships, `tests/test_agent_layer_claims.py` fails until the document is corrected.

| ID | Item | Why | Size |
|---|---|---|---|
| **AGENT-6** | A golden set that blocks a deployment | 200-500 cases per tenant, run on every prompt or model change. It is first here because it is the instrument three other items need: decision 56 cannot be closed without it, P-3 cannot be evaluated without it, and a prompt change ships today on nobody's evidence | M |
| **AGENT-5** | Prompt caching | A cost and latency lever at volume, and there is no volume yet. It moves up the moment a tenant's dossiers are stable and re-read | S |
| **AGENT-3** | The Analyst | The incrementality report states the finding; nothing writes the post-mortem an operator acts on (ADR-043). The constraint is the one the role carries: it may only conclude from a valid experiment | M |
| **AGENT-1** | The Strategist | Recommends a programme and a tier from a dossier. Needs a minimum population size and a prior test, or it is a segmentation machine with no brake | M |
| **AGENT-2** | The Ops agent | Proposes CRM mappings and merges. Mappings are tenant-authored documents today (ADR-014) and merges are reversible by design, so the agent is a proposal layer over machinery that already exists | M |
| **AGENT-7** | The outward MCP server | Read and simulate only by default; any external effect needs explicit approval. Deliberately last: building the interface before the governance it must not bypass is finished is the wrong order, and the product still has six open compliance items | L |

### Product

| ID | Item | Why | Size |
|---|---|---|---|
| **DATA-1** | A second supplier behind `enrich.email` | `docs/07` makes two providers per critical field the condition for GA and `docs/15` ranks single-provider dependence high. One is registered (decision 38); the optimiser cannot reorder around a supplier that is the only one | S — a second document, once a founder picks the supplier |
| **P-1** | Zero-copy over a customer warehouse | ADR-003 stated it in the present tense with no implementation, now corrected (decision 29). It returns as a real item when a partner's DPO or data volume makes the copy the objection | L |
| **P-2** | Program editing beyond the eleven dials | The console emits DSL for money-and-risk parameters (ADR-029). Audience SQL and play copy deliberately stay in a pull request (decision 30). Revisit when an operator asks for a twelfth | M |
| **P-3** | Brand voice classifier trained on the tenant's own material | Today the copywriter is guarded by evals and provenance, not by a model of the customer's voice | L |
| **P-4** | Console for multiple tenants in one view | Every screen is single-tenant. An agency partner needs a switcher | M |

### Signals

| ID | Item | Why | Size |
|---|---|---|---|
| **SIG-2** | A signal that does not beat baseline in 90 days downgrades itself to advisory | `docs/06`'s hygiene section states it in the present tense and nothing does it. It needs per-signal lift against holdout, which the measurement layer can compute per programme and not yet per signal | M |
| **SIG-3** | Per-signal monthly cost and pipeline contribution, and paid signals switched off when they do not cover it | The other two hygiene claims, and they share an instrument with SIG-2. `signal.check` is priced and billed per account-day (`docs/12`), so the cost half is closer than the contribution half | M |

## Built

Delivered and verified against real infrastructure. Grouped by what a buyer would ask about.

- **States that say something.** No renderer leaves a side blank: the Programs list and panel, the Today panel and the Spend panel say what is missing, and five empty panels that carried a zero now carry a sentence. The browser check opens the console as a tenant with nothing and renders every view, requiring a sentence on both sides and no printed missing value (ADR-075, `docs/28` OX-12).\n\n- **First run inside the console.** Five steps under the worklist on Today, each read from the rows it is about - blueprint, connection, programme, signal - with what to do until done, gone once a programme is live. The browser check reads the guide on the fresh tenant, opens the CRM step, and finds the guide gone after the activation (ADR-074, `docs/28` OX-11).\n\n- **Every number defines itself.** One registry in the console, parsed by a test that enumerates every tile and property label; each sentence names what it counts, over what window, from which table, and sits on the element for hover, tap and Enter. The browser check walks every screen for a number without one. Writing the sentences corrected the spend tile that said quarter to date over an all-time sum (D-107; ADR-073, `docs/28` OX-10).\n\n- **The phone approves.** At 390px a tapped row brings the panel to the thumb, the buttons that decide are 40px tall, and the browser check seeds a fresh draft and task, reloads the way a phone opens the console, approves and completes them and reads both back from the database by id (ADR-072, `docs/28` OX-9).\n\n- **A palette that reaches everything.** Every screen from the registry, every programme, every contact by name onto their timeline, and every command from ⌘K; the command that did nothing is gone and a test refuses any handler with an empty body. The browser check reaches the Outbox from the keyboard and a contact by name (ADR-071, `docs/28` OX-8).\n\n- **Bulk, with reasons.** Review, Human tasks and Outbox take a batch: a modified click selects rows, one reason covers them and is recorded on every row with the batch's id, each row runs under its own savepoint so a stale row is a named refusal and the rest proceed, and a thin reason is refused by name before anything happens. `POST /v1/proposals/batch`, `/v1/tasks/batch`, `/v1/outbox/batch` (ADR-070, `docs/28` OX-7).

- **Live, no reload.** Every action re-renders in place from a fresh read of `/v1/console`, the console polls it every thirty seconds while its tab is visible and pays a 304 for a model that has not moved, and fresh data never lands on a field the operator is typing in. No full-page reload remains in the surface; a test greps for it, and the browser check approves a draft and sees the rail count fall without a navigation (ADR-069, `docs/28` OX-6).

- **The report a CFO opens.** Every frozen incrementality report exports as a signed document — `GET /v1/reports/{id}/export`, one link per report on the programme's panel beside the full digest — with an Ed25519 signature over the digest and the rendered prose, made with a key the instance publishes at `/.well-known/zolts-signing-keys.json`. `scripts/verify_report.py` verifies it with `zolts` alone, on a machine that has never seen the database, and names the check that failed (ADR-068, decision 58, `docs/28` OX-5).

- **Which copy works.** Reply and positive-reply rates per step on a programme's detail and at `GET /v1/programs/{id}/copy`, with the sample beside the rate and no rate under the floor `zolts.experiment` accepts: a step with fewer positive replies than the floor shows the count and the reason, a step at the floor shows the rate. A reply is credited to the last step sent before it (ADR-067, `docs/28` OX-4).

- **Which signals earn their keep.** One row per catalogue signal on the Signals screen and at `GET /v1/signals/funnel`: fired, listened to by how many live programmes, enrolled, held out, reached, converted — the silent signals included, and a conversion counted only inside the window the programme it enrolled into declares. Descriptive, not attributed: the holdout converts too and is shown as held out, and SIG-2 stays blocked on the instrument it needs (ADR-066, `docs/28` OX-3).

- **What activating it would do today.** A forecast beside Activate from the functions that will enrol, with the insert taken out: who would enrol, who is held out, week one's steps with sends and credits as an upper bound, capacity left per tier, and the policy rule that would refuse a sample. An unanswerable audience is a refusal, never a zero. A test previews a programme and then really enrols every subject and requires the same count and the same arm (ADR-065, `docs/28` OX-2).

- **Why this person.** One timeline per contact from the six tables that already held it: signal, enrolment, policy decision with rule and pack digest, proposal with evidence and dropped claims, touch with provider and cost, outcome, audit. Newest first, effect above cause, account events through the membership join the worker uses, read on demand. The first half of COMP-2 (ADR-064, D-104, `docs/28` OX-1).

- **A missing field can be bought from the screen that shows it is missing.** `POST /v1/enrich` named the console as its caller and the console never called it: Prospects computed what was missing with the engine's own rule and offered nothing. It now sells the selected rows with the cost on the button, every price read from the price list rather than restated, and a legal basis that is chosen rather than defaulted — including the sentence saying no assessment backs legitimate interest yet (ADR-063, D-103).

- **An action that gave up can be recovered.** The outbox runbook told the operator to requeue a dead action and supplied a raw SQL `update` that nulls `last_error` across a whole channel — destroying the only record of why each one died, at the moment somebody is deciding about it, and reviving the ones whose cause is still there. No screen listed them at all. There is now an Outbox screen with the error that killed each action, the runbook's own triage read off that text, and two acts: put it back on the wire carrying the same idempotency key, or retire it. Both require a written reason and record who acted (ADR-062, D-102).

- **An operator can stop a burning domain.** `runtime/breakers.py` was the only writer of the paused column in the repository, so the only actor that could stop a send was a cut-off firing on rates already earned — and `docs/09` calls that resource the one whose damage is not recoverable on the timescale that matters. Lifting a pause was worse: a flag on the command that registers a domain, so saying the cause was fixed meant restating four authentication facts correctly. Both are now narrow acts on the Sending screen, each requiring a written reason, each recorded. A resume reads the rates first and says whether it agrees with the measurement or overrides a live cut-off, before the click and again in the audit row (ADR-061, D-101).

- **The console opens on the work.** Nine screens answered *what is the state of X*; none answered *what do I do now*, so an operator had to open all nine and join them by hand. Today collects every judgement the runtime already makes — a draft waiting, a task past its SLA, a mailbox in alarm, a tier missing its contractual p95, a cost per contact over target, a programme never activated — ranks them by what ignoring each one costs, and sends the click to the screen that owns the action. Built from the views it summarises rather than from its own queries, so the summary cannot drift from them (ADR-060). The view now also survives a reload, so no action loses your place.

- **The human task queue has a screen.** `GET /v1/tasks` has answered it since D-83 gave a human task a way to be closed, and no screen asked. The console now lists what is waiting for a person, soonest deadline first, says by how much each one is late against the SLA its step stamped, and closes it. A task with no declared SLA is waiting rather than permanently breached. The browser check drives it: a seeded task three hours past due is rendered, marked done, and read back from the database.

- **AGENT-4 · Cost per contact touched is measured.** `runtime.metering.cost_per_contact` divides the token spend of the model kinds by the distinct people reached and reads the quotient against `docs/08`'s target of under €0.02, per programme or across all of them, over a window that narrows both halves together. In `cost_micros`, what the calls cost this business, not the credits the customer is charged. On the spend screen, because it is the margin number of the agent layer and it was on no screen at all.

- **SIG-1 · The ingest path is timed.** `signal.received_at` (migration 029) records when a payload reached this runtime, so `docs/06`'s first time-to-touch column has a probe and all three stages are judged. The arrival is stamped before the transaction opens at both call sites — the push endpoint and the watching pass — so the stage measures the trigger evaluation across every live programme, the part that grows with the programme count. Rows written before the migration carry no arrival time and are excluded rather than counted as instantaneous.

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
| **Signals** | The engine that goes and looks: nine definitions, refresh clocks, decay, dedupe, latency split and per-account-day billing, all executed. **No source ships.** Every definition names a connector — `jobs_feed`, `press_feed`, `product_events` — that no shipped code registers, so `zolts watch` reports `no signal source …` per signal on a real deployment and the engine is verified against a fake (D-67). The machinery is the hard part and it is real; the first partner integration is what makes it produce anything | ADR-022 |
| **Console** | Eight views, no dead links, operable on a phone, and it emits DSL | ADR-023, ADR-028, ADR-029 |
| **Deploy** | Image built, migrated, seeded, preflighted and booted in CI — on the same two commands `fly.toml` runs. On Vercel the same app is one function and the same worker a cron; production is released only by the job that migrates and preflights first, and the function refuses to serve unconfigured with a 503 that says which variable is missing. A probe deployment to the real project built the function with every runtime dependency and proved the rewrite delivers the original path; the minute cron is refused below Pro, as designed | ADR-032, ADR-041, `test_serverless.py` |
| **Restore** | `restore.sh` executed end to end against a real dump, with the failure it guards against reproduced | ADR-033 |
| **Key rotation** | The key sealing every credential can be replaced with no downtime: both keys open during the window, `rotate-key` re-seals resumably, and preflight reports what is still on the old one | ADR-039, `test_key_rotation.py` |
| **Operations** | The liveness endpoint answers 503 when a signal fails, so a monitor pointed at it fires; a worker that stops ticking is reported within five minutes, work or no work; `docs/25` says what to do about each signal, and every command on that page was executed while it was written | D-36, D-39, `docs/25`, `test_runbook.py` |
| **Deployed smoke** | Hourly and strict against the production URL once it is named: every liveness signal, every migration on disk applied, `/api/tick` locked to a stranger. The script itself is executed in tests against live servers in both shapes a deployment takes — the served API and the Vercel entry point — including the release before the secrets exist and a release whose schema is behind its code | `deploy-vercel.yml`, `scripts/smoke_deployed.py`, `test_smoke_deployed.py` |
| **Guards that bite** | Fifteen guards are broken on purpose on every push and a test has to notice. A mutation whose target moved is an error, not a skip | `scripts/mutation_check.py`, `test_mutation_targets.py` |
| **The console on a phone** | Nothing may be drawn on top of text: the check measures where the glyphs start rather than where the box does, which is what made three collisions invisible to every clipping and overflow check | D-38, `browser_console.py` |
| **Admission** | A program is checked where it is stored, not where it arrives: holdout, audience, enrichment pricing and policy overrides run inside `publish`, so signup and the CLI get the same answer as the API | ADR-037, `test_admission.py` |
| **Baseline** | What a tenant cost and produced before Zolts, frozen once at onboarding with a digest the pilot letter quotes; activation without one is noted where an operator looks | ADR-042, `test_baseline.py`, `docs/26` |
| **Model provider disclosure** | What each agent sends and never sends, that the agent layer is off by default, and that the endpoint is a per-deployment control — with the per-tenant promise two documents made and the code did not keep corrected | `docs/11`, D-42, `test_model_provider_disclosure.py` |
| **Incrementality report** | What a program did against its holdout, composed from what the runtime already records and frozen at every period close: arms, lift beside the detectable effect, the unread share, decisions, credits by kind, pipeline on opportunities only and only when significant, the baseline's digest quoted. Written once, the document stored verbatim, a verdict that is never *met* | ADR-043, `test_incrementality_report.py`, `smoke_runtime.py` |
| **A named data supplier** | `enrich.email` is priced, planned by the optimiser and now buyable: Hunter ships as a provider document a tenant registers against their own key, no connector written. A provider's confidence is reported on the scale it was given in, and a registration whose key and document disagree is refused before it is stored | decision 38, D-47, D-48, `test_provider_documents.py` |
| **The CFO's half of the console** | The frozen report is read on the programme it belongs to: verdict, lift beside the detectable effect, digest, credits, and the baseline it quotes. A tenant with none is told when one appears rather than shown an empty panel | ADR-043, decision 39, `browser_console.py` |
| **The metric a programme declares** | What `primary_metric` names is what the measurement counts, inside the window the name carries. A metric the runtime cannot count is refused where the programme is stored; a value metric's amount is reported and never tested | decision 40, D-51, `test_metrics.py` |
| **Published policy packs** | A jurisdiction's rules are a document an operator publishes, one active at a time, and every policy decision cites the version and digest of the pack that produced it — so a decision can be reproduced rather than inferred from today's release | ADR-044, decision 41, D-53, `test_policy_packs.py` |
| **Acquisition cost against the declared ceiling** | The frozen report and the console carry what a meeting this programme caused actually cost — the tenant's own go-to-market run-rate for the period plus the credits Zolts billed, over the *meeting* comparison's increment — beside the ceiling `spec.budget.max_cost_per_meeting` declares. Reported and never enforced: a programme is above it every day until the first meeting lands, so a stop would kill programmes that are working. The ceiling was read by nothing at all (D-63) | decisions 45 and 46, `docs/10` |
| **A period's own spend, declared rather than assumed** | The cost per meeting rested on a monthly figure declared at onboarding and prorated by days, which nothing re-measured: a tenant who grew their team read a figure wrong in a direction nobody could see. `zolts period-spend` records one declaration per billing period, written once and refused once that period's reports are frozen; the report divides by it and names the basis it used. Optional, so a month's close never waits on data entry | decision 46, ADR-046 |
| **The ambient-condition probe, repeatable** | `scripts/ambient_check.py` runs the suite under named ambient conditions and reports the tests whose verdict moves rather than the tests that skip. It found fifteen instances of D-73 that a hand probe over three files had missed. **Not gated**: a condition is a full suite run, so the default pair costs six minutes and the full set fifteen — too slow for every push, and a check that doubles CI is a check somebody deletes. CI covers the highest-value condition another way, by refusing a green run that skipped the isolation tests | `docs/22`, the fifth shape |

## How an item earns its place

The register in `docs/22` is the evidence for this ordering. Forty-two of seventy-five defects were a specification with no caller. Reading found more of them than any other method — thirty-four of the seventy-five — and every one of those was a *document* read against the code — a price nothing charges, an ADR about a connector that does not exist. Not one defect in the register was found by reviewing code for a wrong line.

So an item is only "done" when something runs it and that run can fail. A specification, a document, a column, a button and a process table have each been the whole of a feature in this repository, and each was found by an execution nobody had performed.
