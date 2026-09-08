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

Every program reports a real income statement. What ships is the **incrementality report** (ADR-043): one per program per closed billing period, composed from the tables the runtime already fills and frozen with the period's statement, written once. Its lines, and where each comes from:

| Line | Source | Ships |
|---|---|---|
| Data cost | `cost_event.kind in (enrich.email, enrich.phone, enrich.firmographics)`, in credits billed | yes |
| AI cost | `cost_event.kind in (agent.generate, agent.dossier)` | yes |
| Sending cost | `cost_event.kind = email.send`, plus `program.step` and `signal.check` | yes |
| Human time cost | tasks × configured hourly cost per role | **no** — nothing configures an hourly cost, and a number typed in for the report's sake is the input `docs/17` forbids |
| **Total cost** | sum, in credits; the period's statement prices them | yes |
| Arms, lift and the minimum detectable effect | the experiment, on the primary conversion | yes |
| Incremental conversions | lift × treatment arm, only when significant | yes |
| Incremental pipeline | the *opportunity* comparison's increment × the CRM's own average deal amount, only when that comparison is significant and the CRM holds an amount | yes, or the reason it is withheld |
| — the same figure on screen | the console computes it through the same `zolts.report` rules, so the panel and the frozen document cannot disagree (decision 39) | yes |
| Incremental closed revenue | won deals over the cycle window | **no** — returns when a tenant has held a cycle |
| **Cost per incremental meeting** | credits ÷ incremental meetings | **no** — derivable from the fields above; not stated until a partner has a significant read to divide by |

The report also carries what the number rests on — the unread share of conversions (decision 16), the policy decisions and touches behind it — and quotes the baseline's digest, so the "before" of `docs/17` and this "after" can be laid side by side. It never compares them as a lift: the lift is against the concurrent control, and the baseline says what the same money bought before.

This document, not the number of emails sent, is the product's primary artefact. It moves the conversation from activity to return, and it is what sustains the price.

## The verdict

Three words, decided in `zolts.report.Comparison` by the same rules the measurement endpoint and the console apply:

| Verdict | When |
|---|---|
| `not resolvable` | fewer than five observed conversions in either arm, or an arm with nobody in it: no baseline is established and no effect may be declared |
| `not significant` | the lift sits below the minimum detectable effect for this sample |
| `significant` | the lift clears it |

"Met" is not a verdict. A criterion that is not significant at the end of a term is reported as not significant (`docs/26`, the letter), and the report's own wording is tested for the words that would read otherwise.

## Attribution (as diagnosis, not truth)

Multi-touch attribution is retained for diagnostic questions (which channel appears in won cycles, which sequence precedes a reply), clearly labelled **correlational**. Causal truth comes only from the experiment. This explicit distinction is both a product stance and an honesty stance.

## Anti-patterns blocked by design

| Anti-pattern | Block |
|---|---|
| A leaking holdout (control receives touches from another program) | Cross-program exposure log; contamination reported and excluded |
| Changing the primary metric after the fact | Metric frozen when the version is published |
| Stopping the test on a favourable read | Minimum duration and peeking correction |
| Comparing periods instead of groups | Only concurrent control comparisons are reported |
| Freezing a read when it looks good | A report is frozen only by the close of a billing period, cumulative from the first enrollment; there is no endpoint that freezes one on request (ADR-043) |
| Restating a report after it was read | Written once; the serving role cannot update or delete it; the digest covers inputs and derived figures |
