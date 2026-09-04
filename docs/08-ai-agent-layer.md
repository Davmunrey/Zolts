# 08 — AI agent layer

## The golden rule

> **Agents propose; the runtime disposes.** No agent performs an external action. It emits a `proposed_action` that passes through policy, budget and evaluation before it materialises. This separation is what makes the product sellable to a regulated enterprise.

## Roles

| Agent | Input | Output | Dominant risk | Control |
|---|---|---|---|---|
| **Researcher** | Account plus signals | Dossier with citations and sources | Hallucinated facts | Every claim must cite a retrieved span; without a citation it is dropped |
| **Strategist** | Dossier plus play catalogue | Program and tier recommendation | Over-segmentation | Requires a minimum population size and a prior test |
| **Copywriter** | Dossier, persona, proof points | Message draft | False claims, off-brand tone | Approved claim list, brand voice model, evals |
| **Qualifier** | Inbound replies | Classification plus next action | Misreading a "no" | High threshold for auto-reply; out-of-office and negatives always go to a human |
| **Ops** | Raw data, schemas | Mappings, deduplication, cleanup | Destructive merges | Reversible merges plus a review queue |
| **Analyst** | Results plus holdouts | Post-mortem and recommendation | Correlation read as causation | May only conclude from a valid experiment |

## Anti-hallucination: provenance verification

Every generated message is decomposed into claims. Each claim must map to (a) a span from a retrieved source, (b) a CRM field, or (c) the approved proof library (cases, figures, logos). Claims without provenance are removed from the draft; if removing them breaks the message, it is flagged `needs_human`.

This is stricter than "the LLM usually gets it right", and it is exactly what a CMO demands before letting messages carrying their brand go out.

## Evals: the quality system

| Level | What it measures | Mechanism | Frequency |
|---|---|---|---|
| Unit | Format, length, required fields, no leftover placeholders | Deterministic | Every generation |
| Factuality | Share of claims with provenance | Verifier plus LLM judge | Every generation |
| Brand | Voice adherence, prohibited claims, no superlatives | Per-tenant trained classifier | Every generation |
| Compliance | AI disclosure, opt-out, language, jurisdiction | Deterministic (policy engine) | Every generation, blocking |
| Effectiveness | Positive reply rate versus control variant | Production experiment | Continuous |
| Regression | Golden set of 200-500 cases per tenant | CI, blocks deployment | Every prompt or model change |

**Auto-send gating:** a message goes out without a human only if `eval_score ≥ tier threshold` **and** `policy = allow` **and** the tenant has enabled auto-send for that tier. Defaults: T1 never, T2 0.85, T3 0.90 (higher, because nobody reviews it at scale).

## Model architecture

- **Per-task multi-model router:** classification and extraction on fast, cheap models; strategic reasoning and 1:1 copy on a frontier model. Cost target: under €0.02 in tokens per contact touched.
- **Context by retrieval, not by dumping:** the dossier is assembled through selective retrieval (pgvector plus structured filters). Dumping the whole CRM into the prompt is expensive, noisy and an unnecessary PII exposure.
- **No unnecessary PII to the model:** the minimiser strips fields the task does not require; identifiers are tokenised when the prompt does not need the real value.
- **Prompt caching** for stable dossiers and templates: materially reduces cost and latency at volume.
- **Full traceability:** every piece of content stores model, prompt version, cited sources, evals and approver. An audit requirement under the AI Act.

## Outward agentic interface (MCP)

Zolts exposes an MCP server so a team can operate from its own assistant: query segments, simulate a program, ask for a play's P&L. **Read and simulate only by default**; actions with external effect require explicit approval in the UI. The convenience of "just run it for me from chat" cannot bypass governance.
