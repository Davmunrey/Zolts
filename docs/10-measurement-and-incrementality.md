# 10 — Measurement: incrementality, not attribution

## The problem

Multi-touch attribution answers "who gets the credit?" — a political question. The economic question is "what would have happened if I had done nothing?". The whole sector sells the first. Zolts delivers the second and makes it the argument in front of the CFO.

## Experimental design by default

- **Every program declares a holdout** (minimum 5%; anything lower requires a recorded written justification).
- Deterministic assignment: `variant = hash(entity_id + program_key + salt) % 100 < holdout_pct → control`. Stable across re-runs and auditable.
- Randomisation unit depends on motion: account (B2B), person (B2C), territory or geo (where units contaminate each other, for example local services or ads).
- **Guardrails**: metrics the programme promised not to damage, measured against the same holdout and reported, never enforced (decision 47). This bullet used to say *metrics that automatically stop the experiment if they degrade (spam complaints, unsubscribes, margin per order)* and every clause of it was wrong: nothing stops, and all three examples are outcomes a holdout cannot exhibit. See below.

## What a programme is measured on

The metric a programme declares is the metric it is measured on. That sounds like a restatement and it was not true: every programme was counted on the same three outcome types, at any time after enrolment, whatever `experiment.primary_metric` said (D-51).

A metric name carries two things and both change the answer:

| | |
|---|---|
| **What counts** | `signed_contract_60d` counts a deal marked won. A positive reply is not one, and a programme that says it measures contracts measures contracts or it measures nothing |
| **When it stops counting** | within sixty days of *that account* entering the programme. Without the window, a treatment arm enrolled in January is compared against a control arm still accumulating in June, and the comparison flatters whichever arm has been running longer |

`zolts/metrics.py` is the registry. A name it does not carry is refused where the programme is stored (ADR-037), never measured as something else, and a programme that declares nothing falls back to a named default rather than an implicit one (decision 40).

**Rate metrics and value metrics are not the same measurement.** The two-proportion test reported here asks whether a larger *share* of accounts converted. `net_revenue_28d` is a value metric: its event is counted and tested, and the amount is reported beside it and explicitly not tested — a mean-difference test on a heavy-tailed revenue distribution is different statistics, and claiming it here would be the failure this document exists to prevent.

## Guardrails

A guardrail asks whether winning cost something else, and a holdout answers it the same way it answers the primary metric: both arms are watched, and a difference is a difference. **So a guardrail has to be something the holdout can also exhibit** — a churn, a repeat purchase, a margin. It cannot unsubscribe from an email it was never sent.

That is not a technicality. All four shipped archetypes declared guardrails and the runtime evaluated none of them; when the wiring was written, none of the six names they used existed in the metric registry and four of them could never (D-76, ADR-047). An unsubscribe, a complaint and a bounce are recorded on the **touch**, and the holdout is never touched — that is product invariant 4, not an implementation detail. The control arm of such a comparison is structurally zero, so the verdict is *not resolvable* for ever, and a reader cannot tell that from a programme still gathering data.

Those are real limits and they are already enforced, per mailbox and per domain, by the deliverability rules in `docs/09` and ADR-020: 0.3% complaints pauses a domain, 2% unsubscribes triggers a review. A guardrail is the wrong instrument for them, not a redundant one.

| Refused at admission | Why |
|---|---|
| A name the registry does not carry | Nothing counts it, so it would be a guard that never runs |
| A name the holdout cannot exhibit | Its control arm is zero by construction; the verdict never resolves |
| A value metric | Its amount is reported and explicitly not tested, so a breach in it has no verdict |
| The programme's own primary metric | It cannot disagree with itself |
| The same name twice | One number, two rows, and a reader who counts breaches gets two |

What survives is measured over its own events inside its own window and reported in three words of its own: **held**, **degraded**, **not resolvable**. *Degraded* means the treatment arm did worse by more than the same detectable effect a gain has to clear — a guardrail that fires on noise is one an operator learns to ignore. The primary metric's *significant* is not reused, because it can only ever mean the treatment arm did better, and a reader who saw it beside a guardrail would read it as good news.

**Nothing is paused by this.** The report states what the holdout says and the operator decides, exactly as with the cost ceiling (decision 45). What changed is that a programme winning its primary metric while damaging a guardrail no longer reports an unqualified win: the qualification sits in the verdict paragraph, where a reader who stops after one paragraph will see it.

**Three of the four shipped archetypes declare none.** The guardrails they want are churn, margin and rep time, and none is an outcome this runtime records. Declaring one anyway would be a specification with no caller, which is this repository's commonest defect — so they say what they cannot measure, and why, in the programme file.

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
| **Cost per incremental meeting** | the tenant's declared monthly go-to-market spend, prorated over the period from the frozen baseline, plus the credits Zolts billed — divided by the *meeting* comparison's increment, only when that comparison is significant | yes, or the reason it is withheld |
| — against the ceiling the programme declared | `spec.budget.max_cost_per_meeting`, carried in the report so a reader holding the signed document does not also need the programme. Reported and never enforced (decision 45) | yes, when one is declared |
| — on the same basis as the baseline | credits alone would be €0.42 a meeting beside a baseline of €1,297 a meeting and a ceiling a shipped programme declares at €180: three figures in one document that are not the same measurement (decision 46) | yes |
| **Guardrails** | each declared guardrail against the same concurrent holdout, over its own events inside its own window; a breach qualifies the verdict paragraph (decision 47) | yes, or that none was declared |
| — declared for the period, or prorated from onboarding | a declaration for the period (`zolts period-spend`) when there is one; otherwise the onboarding run-rate prorated by days, which nothing re-measures. `own_spend_basis` names which, and the document disclaims the assumption and does not disclaim the measurement. No figure exists without one of the two (ADR-046) | yes |

The report also carries what the number rests on — the unread share of conversions (decision 16), the policy decisions and touches behind it — and quotes the baseline's digest, so the "before" of `docs/17` and this "after" can be laid side by side.

**Two checks, and the document says which version it was written in.** A holder can hash the JSON they were given and compare it with the digest in the letter; that is integrity, and it never depended on our code. They can also rebuild the report through `zolts.report.from_mapping` and recompute both the figures and the hash; that is what makes it auditable rather than merely intact. The second check broke the first time the canonical form grew a field — an older body rebuilt under a newer shape hashed differently from its own letter (D-72) — so the form carries a `schema_version`, every earlier key set is kept, and a rebuild recomputes the shape the document declares. A body with no version key is version 1. It never compares them as a lift: the lift is against the concurrent control, and the baseline says what the same money bought before.

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
