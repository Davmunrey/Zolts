# 24 · Backlog

What is built, what is next, and what is blocked on a decision rather than on engineering.

**The single fact that orders everything below: the runtime is not deployed.** Every item in "Built" is verified against real infrastructure and reachable by nobody. Until three secrets are set, nothing else on this list changes the company's position.

## Status at a glance

| | Count | Evidence |
|---|---|---|
| Epics delivered | 37 | `docs/22`, ADR-001 … ADR-039 |
| Tests | 975 | 327 against a real Postgres; CI fails a run that skipped them |
| Priced actions executable | 8 of 8 | `docs/12` vs `zolts/billing.py`, checked by test |
| Console views | 8, no dead links | ADR-023 |
| Native CRMs | 3 | HubSpot, Pipedrive, Salesforce — all three read deals |
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
| **SEC-2** | Backup schedule and a restore drill on a real managed Postgres | The restore script is now executed in tests (ADR-033) against a local cluster. It has never run against Neon | S |
| **SEC-3** | Document what a model provider sees, and the gateway option | Enterprise diligence asks. `base_url` is configurable (ADR-011); nobody has written down that it is a control | XS |
| **OPS-3** | Route the 503 somewhere that wakes a person | `/health/liveness` now answers 503 and `docs/25` says what to do about each signal, so this is a URL in an uptime checker rather than a project — but it is not done until a real monitor is pointed at a real deployment | XS |

### Verification

| ID | Item | Why | Size |
|---|---|---|---|
| **VER-1** | Measure mutation coverage across the runtime | The suite reports 975 tests. That is a count of intention, not of coverage. The real number is what fraction bites when the thing it guards is broken — five of the register's defects were found exactly there, and each new guard is now mutation-verified by hand, which does not scale | M |
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
| **Deals** | Accounts already in a live sales conversation are excluded from outbound, and a CRM that cannot say which asks the program to refuse rather than assuming none | ADR-038, `test_opportunity.py` |
| **Deliverability** | Per-domain capacity, warmup, circuit breakers, mailbox fleet with reputation kept where it was earned | ADR-020 |
| **Enrichment** | Waterfall bought in the declared order with measured hit rates; a miss is absorbed, not billed | ADR-021 |
| **Signals** | Nine definitions; the runtime goes and looks rather than waiting to be told; priced per account-day | ADR-022 |
| **Console** | Eight views, no dead links, operable on a phone, and it emits DSL | ADR-023, ADR-028, ADR-029 |
| **Deploy** | Image built, migrated, seeded, preflighted and booted in CI — on the same two commands `fly.toml` runs | ADR-032 |
| **Restore** | `restore.sh` executed end to end against a real dump, with the failure it guards against reproduced | ADR-033 |
| **Key rotation** | The key sealing every credential can be replaced with no downtime: both keys open during the window, `rotate-key` re-seals resumably, and preflight reports what is still on the old one | ADR-039, `test_key_rotation.py` |
| **Operations** | The liveness endpoint answers 503 when a signal fails, so a monitor pointed at it fires; `docs/25` says what to do about each signal, and every command on that page was executed while it was written | D-36, `docs/25`, `test_runbook.py` |
| **Admission** | A program is checked where it is stored, not where it arrives: holdout, audience, enrichment pricing and policy overrides run inside `publish`, so signup and the CLI get the same answer as the API | ADR-037, `test_admission.py` |

## How an item earns its place

The register in `docs/22` is the evidence for this ordering. Twenty-four of thirty-four defects were a specification with no caller. Reading found eight of them and every one of those was a *document* read against the code — a price nothing charges, an ADR about a connector that does not exist. Not one defect in the register was found by reviewing code for a wrong line.

So an item is only "done" when something runs it and that run can fail. A specification, a document, a column, a button and a process table have each been the whole of a feature in this repository, and each was found by an execution nobody had performed.
