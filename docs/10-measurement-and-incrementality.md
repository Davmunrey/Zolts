# 10 — Measurement: incrementality, not attribution

## The problem

Multi-touch attribution answers "who gets the credit?" — a political question. The economic question is "what would have happened if I had done nothing?". The whole sector sells the first. Zolts delivers the second and makes it the argument in front of the CFO.

## Experimental design by default

- **Every program declares a holdout** (minimum 5%; anything lower requires a recorded written justification).
- Deterministic assignment: `variant = hash(entity_id + program_key + salt) % 100 < holdout_pct → control`. Stable across re-runs and auditable.
- Randomisation unit depends on motion: account (B2B), person (B2C), territory or geo (where units contaminate each other, for example local services or ads).
- **Guardrails**: metrics that automatically stop the experiment if they degrade (spam complaints, unsubscribes, margin per order).

## Metrics

```
absolute_lift        = treatment_rate − control_rate
relative_lift        = absolute_lift / control_rate
incremental_pipeline = absolute_lift × N_treatment × average_opportunity_value
program_roi          = (incremental_margin − program_cost) / program_cost
```

Statistical power: the system computes **before publishing** how many weeks are needed to detect the minimum relevant lift (MDE). If a program cannot reach significance in a reasonable window, it says so explicitly and suggests grouping programs into an umbrella experiment. Reporting a non-significant lift as a success is the most common failure of GTM teams.

For long cycles (over six months), validated proxy metrics are used against history: qualified meeting → opportunity → close, with the tenant's own conversion rates, and the confidence interval is always reported, never the point estimate.

## Per-play P&L

Every program reports a real income statement:

| Line | Source |
|---|---|
| Data cost | `cost_event.kind = enrichment` |
| AI cost | `cost_event.kind = llm` |
| Sending and ads cost | `cost_event.kind in (send, ads)` |
| Human time cost | tasks × configured hourly cost per role |
| **Total cost** | sum |
| Incremental pipeline | from the experiment |
| Incremental closed revenue | from the experiment, over the cycle window |
| **ROI and cost per incremental meeting** | derived |

This view, not the number of emails sent, is the product's primary screen. It moves the conversation from activity to return, and it is what sustains the price.

## Attribution (as diagnosis, not truth)

Multi-touch attribution is retained for diagnostic questions (which channel appears in won cycles, which sequence precedes a reply), clearly labelled **correlational**. Causal truth comes only from the experiment. This explicit distinction is both a product stance and an honesty stance.

## Anti-patterns blocked by design

| Anti-pattern | Block |
|---|---|
| A leaking holdout (control receives touches from another program) | Cross-program exposure log; contamination reported and excluded |
| Changing the primary metric after the fact | Metric frozen when the version is published |
| Stopping the test on a favourable read | Minimum duration and peeking correction |
| Comparing periods instead of groups | Only concurrent control comparisons are reported |
