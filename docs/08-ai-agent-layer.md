# 08 — AI agent layer

## The golden rule

> **Agents propose; the runtime disposes.** No agent performs an external action. It emits a `proposed_action` that passes through policy, budget and evaluation before it materialises. This separation is what makes the product sellable to a regulated enterprise.

## Roles

Six were designed and **three run**. The other three are on this page because
the design still holds, not because the product has them, and a table that did
not say so was a table a technical buyer would ask to see three times and be
shown nothing (D-93).

| Agent | Status | Input | Output | Dominant risk | Control |
|---|---|---|---|---|---|
| **Researcher** | **Built** | Account plus signals | Dossier with citations and sources | Hallucinated facts | Every claim must cite a retrieved span; without a citation it is dropped |
| **Copywriter** | **Built** | Dossier, persona, proof points | Message draft | False claims, off-brand tone | Approved claim list, evals, provenance, discount authority. The brand *voice model* is not built — P-3 |
| **Qualifier** | **Built** as `runtime/agents/triage.py` | Inbound replies | Classification plus next action | Misreading a "no" | A verdict must quote the reply, and an unverifiable quote is discarded (ADR-016). Negatives and unsubscribes go through the same suppression path a provider's event takes |
| **Strategist** | **Not built** | Dossier plus play catalogue | Program and tier recommendation | Over-segmentation | Would require a minimum population size and a prior test. AGENT-1 |
| **Ops** | **Not built** | Raw data, schemas | Mappings, deduplication, cleanup | Destructive merges | Mappings are authored by the tenant as documents today (ADR-014); nothing proposes one. AGENT-2 |
| **Analyst** | **Not built** | Results plus holdouts | Post-mortem and recommendation | Correlation read as causation | The incrementality report states the finding; nothing writes the post-mortem (ADR-043). AGENT-3 |

## Anti-hallucination: provenance verification

Every generated message is decomposed into claims. Each claim must map to (a) a span from a retrieved source, (b) a CRM field, or (c) the approved proof library (cases, figures, logos). Claims without provenance are removed from the draft; if removing them breaks the message, it is flagged `needs_human`.

This is stricter than "the LLM usually gets it right", and it is exactly what a CMO demands before letting messages carrying their brand go out.

## Commercial authority: what a message may offer

Provenance asks whether a claim is **true**. It does not ask whether we are **allowed to say it**, and those come apart on exactly one kind of sentence: an offer. A blueprint declares `policy.discount_authority` — `ecommerce-dtc` permits 15% — and a message that cites the promotion perfectly and offers 25% is correctly sourced and still commits the seller to terms the archetype forbids. Nothing read that ceiling until D-85, and provenance hid the gap: an *unevidenced* offer was already stopped as an unsupported quantified claim, so only the evidenced one ever got through.

An offer over the ceiling stops the send and is listed for the reviewer. It is **never removed** the way an unsupported claim is: deleting the offer and sending the rest is precisely the case this page already sends to a person, and a reviewer who cannot see the offer cannot judge it. A currency amount is stopped too, because a percentage ceiling cannot compare one without an order value (ADR-052, decision 52).

A figure is not an offer until a word makes it one — *20% of our customers renew early* is a fact — and a percentage at or below the ceiling passes whatever the sentence says, so only a figure above the archetype's own limit can ever cost a review.

## Evals: the quality system

| Level | What it measures | Mechanism | Frequency |
|---|---|---|---|
| Unit | Format, length, required fields, no leftover placeholders | Deterministic | Every generation |
| Factuality | Share of claims with provenance | Verifier plus LLM judge | Every generation |
| Brand | Prohibited claims, superlatives, required markers | **Deterministic rules, not a classifier.** The per-tenant trained voice model is P-3 | Every generation |
| Compliance | AI disclosure, opt-out, language, jurisdiction | Deterministic (policy engine) | Every generation, blocking |
| Effectiveness | Positive reply rate versus control variant | Production experiment | Continuous |
| Regression | Golden set of 200-500 cases per tenant | **Not built.** No golden set exists and nothing blocks a deployment on prompt or model quality. AGENT-6, and it is what decision 56 is waiting for | — |

**Auto-send gating:** a message goes out without a human only if `eval_score ≥ tier threshold` **and** `policy = allow` **and** the tenant has enabled auto-send for that tier. Defaults: T1 never, T2 0.85, T3 0.90 (higher, because nobody reviews it at scale).

## Model architecture

- **Context by retrieval, not by dumping** — *built*: the dossier is assembled from the account, the contact and the signals that enrolled them, not from the CRM record. `docs/11` states exactly what each agent sends and never sends.
- **Full traceability** — *built*: every proposal stores the model, the prompt version, the evidence it was written from, the eval scores and who or what approved it. An audit requirement under the AI Act.
- **Per-task multi-model router** — *not built*: every agent starts on the frontier model, including the Qualifier, whose job is classification. The only downgrade that exists is a **spend-guard refusal offering a cheaper alternative**, which is a different mechanism with a different trigger — it fires when a tenant is near a ceiling, not when a task is cheap. `CHEAP_MODEL` was imported by the copywriter and used nowhere (D-93). Routing classification to it is decision 56, and it is not a free win: a cheaper model that misreads *take me off your list* is the D-16 failure, and there is no golden set to measure that against yet.
- **Cost target under €0.02 in tokens per contact touched** — **Built** (AGENT-4). `runtime.metering.cost_per_contact` divides the token spend of the model kinds — `agent.generate` and `agent.dossier`, at `cost_event.cost_micros`, which is what the calls cost this business rather than what the customer is charged — by the distinct people reached, and reads the quotient against this target. Per programme, or across all of them, over a window that narrows both halves together. *Under* is strict: exactly two cents misses. A sent touch predating `touch.person_id` names nobody and cannot enter a count of people, so it is excluded and counted separately — that understates the denominator and overstates the cost, which is the direction that cannot make a missed target look met.
- **No unnecessary PII to the model** — *true by construction, not by a component*: there is no minimiser and no tokenisation. What is true is that the agents assemble a retrieved subset rather than a record, which `docs/11` enumerates field by field. A sentence describing a component nobody wrote is the more dangerous half of a claim that happens to hold.
- **Prompt caching** — *not built*: AGENT-5. It is a cost and latency lever at volume and there is no volume yet.

## Outward agentic interface (MCP) — designed, not built

**There is no MCP server in this repository.** The design stands: a team operates from its own assistant, querying segments, simulating a programme, asking for a play's profit and loss, **read and simulate only by default**, with any external effect requiring explicit approval in the product. The convenience of "just run it for me from chat" cannot bypass governance, and building it before the governance it must not bypass is finished would be the wrong order. AGENT-7.

Zolts *consumes* an MCP server today — the spend guard is one (ADR-010) — which is the opposite direction and is easy to read as this claim being met.
