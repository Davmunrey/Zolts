# 24 · Backlog

What is built, what is next, and what is blocked on a decision rather than on engineering.

**The single fact that orders everything below: the runtime is not deployed.** Every item in "Built" is verified against real infrastructure and reachable by nobody. Until three secrets are set, nothing else on this list changes the company's position.

## Status at a glance

| | Count | Evidence |
|---|---|---|
| Epics delivered | 33 | `docs/22`, ADR-001 … ADR-033 |
| Tests | 861 | 300 against a real Postgres; CI fails a run that skipped them |
| Priced actions executable | 8 of 8 | `docs/12` vs `zolts/billing.py`, checked by test |
| Console views | 8, no dead links | ADR-023 |
| Native CRMs | 3 | HubSpot, Pipedrive, Salesforce |
| Sending channels | **1** | Blocked on decision 27 |
| Production tenants | **0** | Blocked on B-1 |

## Now — the only thing that matters this week

| ID | Item | Why | Size | Blocked by |
|---|---|---|---|---|
| **B-1** | **Deploy the runtime to Fly + Neon** | Everything built is unreachable. A product in production with one channel is worth more than a perfect one with no users | S — the path is written and executed to the boundary of the account (ADR-032) | **Founder**: `ZOLTS_DATABASE_URL`, `ZOLTS_APP_DATABASE_URL`, `ZOLTS_SECRET_KEY` via `fly secrets set`. Never through chat, an issue, or a file |
| **B-2** | Run `smoke_deployed.py --strict` against the live URL | The difference between the process being up and the product answering | XS | B-1 |
| **B-3** | First paying partner onboarded end to end | The only validation that matters. Invitation → redeem → connect CRM → activate → first send | M | B-1 |

**Acceptance for B-1:** `preflight` passes in production mode against the real database; `/health` reports every migration applied; the worker process drains; one program activates and one action reaches a provider.

## Next — blocked on a founder decision, not on engineering

| ID | Item | Default if undecided | Decision |
|---|---|---|---|
| **B-4** | Second sending channel | It does not exist; a LinkedIn step becomes a person's task. The seam is built, so making it real is a definition plus a connector, not a rewrite (ADR-027) | **27** — price and vendor are one decision |
| **B-5** | Repository visibility | Open. `docs/12` (pricing, COGS, margins) and `docs/15` (risk matrix) are world-readable, and `zolts.vercel.app` serves the demo with no authentication | **Founder**, `docs/23` |
| **B-6** | Data residency per tenant | Single region, `mad`, pinned in `fly.toml` | `docs/18` |

## Then — engineering, unblocked, ordered by what being wrong costs

### Security and operations

| ID | Item | Consequence of not doing it | Size |
|---|---|---|---|
| **SEC-1** | Key rotation for sealed connector credentials | One key compromise decrypts every credential for every tenant, and today there is no tested path to rotate | M |
| **SEC-2** | Backup schedule and a restore drill on a real managed Postgres | The restore script is now executed in tests (ADR-033) against a local cluster. It has never run against Neon | S |
| **SEC-3** | Document what a model provider sees, and the gateway option | Enterprise diligence asks. `base_url` is configurable (ADR-011); nobody has written down that it is a control | XS |
| **OPS-1** | Alerting on the outbox: stalled actions, dead letters, breaker trips | `liveness` reports them and nothing watches it | S |
| **OPS-2** | Runbook: what an operator does when a domain burns, a provider 429s, or the queue stalls | The knowledge exists in ADRs, not in a page somebody follows at 3am | S |

### Verification

| ID | Item | Why | Size |
|---|---|---|---|
| **VER-1** | Measure mutation coverage across the runtime | The suite reports 840 tests. That is a count of intention, not of coverage. The real number is what fraction bites when the thing it guards is broken — and five defects this session were found exactly there | M |
| **VER-2** | Visual regression on the console | Two defects this session were visible on a screenshot and invisible to every test (D-15, D-16) | M |
| **VER-3** | Deployed-environment smoke on a schedule | CI proves the image works. Nothing proves the deployment still does | S |

### Product

| ID | Item | Why | Size |
|---|---|---|---|
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
| **Deliverability** | Per-domain capacity, warmup, circuit breakers, mailbox fleet with reputation kept where it was earned | ADR-020 |
| **Enrichment** | Waterfall bought in the declared order with measured hit rates; a miss is absorbed, not billed | ADR-021 |
| **Signals** | Nine definitions; the runtime goes and looks rather than waiting to be told; priced per account-day | ADR-022 |
| **Console** | Eight views, no dead links, operable on a phone, and it emits DSL | ADR-023, ADR-028, ADR-029 |
| **Deploy** | Image built, migrated, seeded, preflighted and booted in CI — on the same two commands `fly.toml` runs | ADR-032 |
| **Restore** | `restore.sh` executed end to end against a real dump, with the failure it guards against reproduced | ADR-033 |

## How an item earns its place

The register in `docs/22` is the evidence for this ordering. Twenty-two of thirty defects were a specification with no caller, and every high-cost one was found by executing or mutating rather than by reading.

So an item is only "done" when something runs it and that run can fail. A specification, a document, a column, a button and a process table have each been the whole of a feature in this repository, and each was found by an execution nobody had performed.
