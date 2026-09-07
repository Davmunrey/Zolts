# 22 · Defect register

Every defect this repository has found in itself, what it would have cost, and what now stops it returning.

**None of these reached a customer.** There is no production deployment and no customer data (`docs/20`). They are development defects, and the register exists because *how* they were found is the more useful artefact than the list itself.

## The finding method, and its yield

| How | Defects found | What it means |
|---|---|---|
| **Executed** | 16 | Ran the thing against real infrastructure — real Postgres, real browser, real container |
| **Mutated** | 6 | Broke a guard on purpose to see whether it bites |
| **Looked** | 4 | Rendered a screen and read the screenshot |
| **CI** | 4 | An existing check fired |
| **Read** | 3 | Found by reading code or a document against reality |

**Reading found the fewest.** Every high-cost defect below — money billed wrongly, a live program's spec overwritten, an opt-out counted as a conversion — was found by executing or by mutating, not by review. That is the single most transferable finding in this register.

## The recurring shape

Twenty-four of the thirty-three are one defect wearing different clothes: **a complete specification with no caller.** A price in `docs/12` nothing charges. A column no code sets. A button labelled for a feature that does not exist. A process table CI never runs. A restore script that ends by telling you to test it.

The second shape, found only once the first was exhausted: **a guard that passes when you break the thing it guards.** Four of those, and one of them was a test written in this same session.

## Register

Severity is the cost of the defect reaching a paying customer, not the cost of fixing it.

### Money

| ID | Defect | Blast radius | Found | Fix | Guard |
|---|---|---|---|---|---|
| D-03 | `cost_event.billed_credits` existed from the first migration; no caller ever set it. `docs/12` priced eight actions; the runtime billed nothing | **Critical** — the entire product was free | Read | `zolts/billing.py` holds the list; metering writes it | A test parses `docs/12` and asserts the code agrees (ADR-017) |
| D-04 | A credit was recorded only `if result.cost_micros`, so a send through a provider that reports no COGS was free to the customer | **Critical** — revenue silently lost per provider | Executed | Billing does not depend on knowing our own cost | ADR-017 |
| D-05 | The overage ladder and the extra seat were priced in `docs/12` and charged by nothing; the runtime reported the rate as "contractual" | **Critical** — expansion revenue, the first NRR driver, was arithmetic nobody could run | Read | Both charged, graduated over the overage alone | A test re-reads each number out of `docs/12`; a property test asserts the bill never falls as consumption rises |
| D-06 | Every tenant stopped at their included credits — a ceiling that cannot be raised, so overage was unreachable in the product | **High** — no tenant could overspend, so none could expand | Executed | `tenant.credit_ceiling`, defaulting to the included allowance | ADR-017 |
| D-08 | Every dispatch was priced at `email.send`. `task` and `crm` both have providers, so a HubSpot task written for a salesperson was billed to the customer as **an email they never sent** | **Critical** — an invoice a customer can disprove | Executed | `runtime/channels.py`: a channel carries its own credit kind | ADR-027; four call-site mutations, all caught |
| D-11 | `program.step`, the highest-volume priced action, was billed by nothing | **Critical** — the volume line of the price list was free | Read | `metering.meter_step`, charged when the runtime ran it | ADR-024 |
| D-12 | `agent.dossier` was priced at 20 credits and was neither executable nor stored | **High** — a priced artefact that did not exist | Read | `runtime/research.py`, stored and reused | ADR-025 |
| D-30 | The application role lacked `usage` on a `bigserial` sequence, so an insert was denied | **High** — a feature that worked in development and failed for every tenant | Executed | `grant_app_role` grants on all sequences | A test asserts every sequence is usable by the app role |

### Correctness and measurement

| ID | Defect | Blast radius | Found | Fix | Guard |
|---|---|---|---|---|---|
| D-01 | The internal schema was named `zolts` beside a role named `zolts`. Postgres resolves `"$user"` before `public`, so a second migration run created a complete duplicate of every table in the shadowing schema — silently, reporting success | **Critical** — queries returning the wrong data with no error | Executed | Schema is `zolts_internal`; every connection pins `search_path` | ADR-009, plus a test that the schema is not named after a plausible role |
| D-02 | Every reply was recorded as `reply_positive`. "Take me off your list" was counted as a conversion and the person left contactable | **Critical** — a compliance failure and an unbounded inflation of the only number the product sells | Executed | Triage classifies; the runtime disposes; a verdict must quote the reply | ADR-016; the unread share is reported beside the lift |
| D-20 | `programs.publish` was a plain upsert. Republishing v2.1.0 with different content replaced the spec of a version already live, under every enrollment running on it | **Critical** — the audit log says one thing, the program says another, and one measured lift covers two programs | **Mutated** | A version is written once; identical content still succeeds | ADR-030; 409 on conflict |
| D-22 | `policy_decision.rationale` was nullable and `record_decision` took `str \| None`. The gate could record a deny with no reason and the entire suite passed | **High** — the console renders a decision nobody can explain, on the screen a regulated buyer opens first | **Mutated** | Migration 018: `not null` plus a non-blank check; `DecisionWithoutAReason` | ADR-031 |
| D-29 | `now()` is the transaction timestamp, so two dossiers written in one transaction shared it exactly and "the latest" was undecidable | **Medium** — a stale dossier served as current | Executed | A `bigserial` decides order | A test asserts one distinct `created_at` while `latest()` still returns the second |
| D-07 | The sync never read the connection's `config` column, so a CRM with a per-org host was unreachable | **Medium** — Salesforce could not be connected | Executed | `from_config` on the source contract | ADR-018 |
| D-10 | `linkedin`, `ads` and `webhook` were dispatchable with no provider. The shipped flagship program's four LinkedIn steps queued, failed as permanent errors and were cancelled one at a time | **High** — loud in a row nobody reads is silent | Executed | An unexecutable channel becomes a person's task | ADR-027 |
| D-09 | Every dispatch allocated a mailbox seat, so a CRM task consuming no sending reputation was held by the mailbox daily cap | **Medium** — work stalled by a limit from a channel it is not on | Executed | Only channels that use the fleet allocate | ADR-027 |
| D-28 | `console.build` was handed a label dictionary instead of the tenant's row; the spend view reads a billing period keyed by id | **Low** — a smoke script passed, CI failed | CI | `build` raises a named error when the tenant has no id | A test holds the stub deliberately |

### Product surface

| ID | Defect | Blast radius | Found | Fix | Guard |
|---|---|---|---|---|---|
| D-13 | Five rail entries led nowhere | **High** — a nav that promises what it cannot do | Looked | Every entry has a view | ADR-023 |
| D-14 | Five console views wore the Programs header over columns sized for a different table, so values wrapped and rows grew into each other | **Medium** | Looked | One `--cols` shared by header and rows | A test asserts each view labels exactly the columns it has |
| D-15 | At 390px the rail, the nav and the detail panel were `display:none` and 18 elements were clipped: one screen with half a table on it | **High** — the product was unusable on a phone | Looked | Rail as a scrolling strip; rows stack into label/value pairs | `browser_console.py` fails on an unreachable rail, a clipped cell or horizontal overflow at 390px |
| D-16 | Text columns were fixed pixel widths. A 1920px display gave ~500px of spare width to empty gutter while `local-services-multisite` was cut off inside a 152px column | **Medium** | Looked | `minmax(px, fr)` for variable-length text; `w:` declares prose | ADR-028; a wide-screen check at 1920px |
| D-19 | ADR-002 said "the UI generates DSL". Nothing did. The button was labelled *Open in editor* and did nothing | **High** — a documented architecture decision with no implementation, read by diligence | Read | Eleven money-and-risk dials emitting a whole document | ADR-029; the browser publishes and the assertion is in SQL |

### Guards that did not guard

The category that only appears once you go looking for it.

| ID | Defect | Blast radius | Found | Fix |
|---|---|---|---|---|
| D-17 | `browser_console.py` detected the wide-screen clipping, printed it, and returned 0 | **High** — worse than no check: a defect reported and a build gone green | Mutated | Returns 1 |
| D-23 | Nothing stopped an agent importing a connector and sending directly. It happened not to — a fact about four files, not a property of the system | **Critical** if it had ever been relied on — an email nobody approved | Mutated | No module under `runtime/agents/` may import the machinery that acts on a prospect |
| D-24 | CI built the image, booted it and curled it — all on the image's default CMD. `fly.toml`'s two process commands were run by nothing, and the worker is the product | **High** — a deploy with an API that answers and a worker that crashloops; the symptom is a queue that grows with no error | Read | A test reads the process table; CI runs both commands in the container |
| D-25 | `scripts/restore.sh` had never been executed. Two tests asserted the *reasoning* behind its first step | **Critical** — a restore happens after a customer's data is gone | Read | The script is run end to end against a real dump |
| D-26 | The first version of that restore test restored into a new database **on the same cluster**, where the role already exists, so the condition the script guards against never arose. Deleting the whole role-creation step left every test passing | **High** — a test that certified a backup nobody had tested | Mutated | The dump is rewritten to grant to a role the cluster does not have |
| D-27 | The step-3 test faked a stale dump by deleting a bookkeeping row, producing a state that cannot occur, and its failure was first read as a defect in migration 018 | **Low** — a wrong diagnosis, corrected before it became a wrong fix | Executed | A real predecessor is built: every migration but the last, applied and recorded |
| D-18 | `site/` was stale against `design/console.html` | **Low** | CI | The build is regenerated and CI fails on drift |
| D-32 | `scripts/smoke_runtime.py` returned `0` unconditionally. Its docstring said it proved "the whole loop is connected"; it printed a report and exited zero with nothing enrolled, nothing sent and no decision recorded. It runs in CI as the one end-to-end check | **Critical** — the single check standing between a disconnected product and a green build could not fail | Executed | Each stage must have happened, and a missing one names itself | Verified by breaking the fixture: the account no longer matches the audience and the run exits 1 naming four dead stages |
| D-33 | `spec.enrich` is never read by the runtime. Enrichment happens only when an operator types a CLI command, and the fields the shipped programs request — `work_email`, `linkedin_urn`, `tech_stack` — are not the fields the price list carries, which are `email`, `phone` and `firmographics`. It could not have worked if it had been called | **High** — the waterfall is priced, tested and measured, and no program triggers it. For a Clay-class product this is the core loop | Executed | **Open.** Registered as decision 33 with the vocabulary conflict stated | — |
| D-31 | One subprocess call in the new restore tests inherited the environment without the `ZOLTS_SECRET_KEY` fallback its siblings carry. `Settings.from_env` builds the whole configuration before dispatching, so even `migrate` refuses without a key it never uses. It passed locally, where the variable is exported, and failed in CI, where it is not | **Low** — but the class is not: a test whose requirements are undeclared certifies the machine it ran on, not the code | CI | One helper declares what every subprocess in the file needs; the failure was reproduced with `env -u ZOLTS_SECRET_KEY` before and after |
| D-21 | ADR-003 claimed zero-copy over the customer's warehouse in the present tense. The word `warehouse` appears nowhere in `runtime/` | **High** — a security review asks to see the connector and there is none | Read | The document is corrected to what ships (decision 29) | A test fails in both directions: while nothing reads a warehouse the document must say so, and the day something does the document is stale |

## What this register is not

It is not a list of production incidents. There have been none, because there is no production. The first entry in this table that involves a customer will be of a different kind, and the honest expectation is that it will be found the same way: by running something, not by reading it.
