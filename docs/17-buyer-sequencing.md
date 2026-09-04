# 17 — Buyer sequencing: operator and CFO

## The question

"Why not both?" — operator (RevOps/GTM Engineer) and CFO simultaneously.

## The short answer

**Both, yes, as a buying centre. No, not as a simultaneous motion.** They are not two segments: they are two roles on the same committee, entering at different moments. What cannot be duplicated at an early stage is not the narrative — it is the **product surface, the price anchor and the cash cycle**.

## What they share (marginal cost ≈ 0, built once)

| Component | Sells to the operator as… | Sells to the CFO as… |
|---|---|---|
| Holdout engine | Proof their play works | Justified spend control |
| Per-play P&L | Diagnosis of what to cut | A GTM income statement |
| Policy engine | Avoids burning domains | Reduces regulatory risk |
| Cost ledger | Program budget | Spend under management |

Seventy per cent of the product is buyer-agnostic. There, "both" is free and mandatory.

## What forks (real cost, forces a choice)

| Dimension | Operator | CFO | Both at once with 6-8 people? |
|---|---|---|---|
| **First experience** | Builder: a live program in under 60 minutes | Diagnostic: spend audit plus business case | **No** — two distinct first-run products |
| **Price anchor** | €490-1,490/month plus credits, self-serve | €8-40k/year on a share of governed GTM spend | **No** — coexistence destroys the high anchor |
| **Time to value** | 7 days | One full sales cycle (60-120 days) | **No** — incompatible cash cycles |
| **Required proof** | A program that works | Measured baseline plus significant lift | **No** — the second depends on the first |
| **Roadmap** | Studio, CLI, blueprints, speed | Procurement, SSO, SOC 2, residency, consolidated reporting | **No** within 90 days |
| **Hiring** | Growth engineer / self-serve | AE selling at C level | **No** before month 7 |

## The forced sequence (preference does not enter into it)

Selling to the CFO requires three assets that **exist only through execution**: (1) a baseline of current GTM spend, (2) lift measured against holdout over at least one cycle, (3) real cost per meeting and per euro of pipeline. Selling to the CFO first means selling a business case with no data — precisely what every competitor does, and precisely why nobody believes them.

**Act 1 (months 0-12) — Land with the operator.** Credits, speed, adoption. The real objective is to accumulate execution, the raw material of the moat.

**Act 2 (months 9-24) — Expand to the CFO.** Not a new segment: an upsell inside an existing account, with the proof already accumulated. Conversion from a consumption contract to a platform contract with spend governance, at three to five times the ACV.

The acts overlap by three months deliberately: in month 9 the CFO pitch is tested on customers who already hold six months of data.

## The irreversible phase 1 requirement

> **Capture the baseline and the spend ledger from day one of every tenant, even though nobody sells on it until month 9.**

Reason: a baseline cannot be reconstructed retroactively. If the "before" does not exist in month 9, Act 2 either collapses or requires re-instrumenting and waiting another full cycle. Cost of doing it now: roughly 10-15% of engineering across phases 1-2. Cost of not doing it: six to nine months of delay in Act 2.

Captured during the first onboarding, with no user friction:

- Current spend by tool and by channel (declared at onboarding, four fields).
- Volume and conversion rates for the prior 90 days (imported from the CRM).
- Cost per meeting and per opportunity before Zolts.
- A frozen, signed snapshot: this is the Act 2 document.

## The dual-buyer trap (and its guardrail)

Risk: building a builder too technical for the CFO and a governance layer too heavy for the operator, ending with a product that is mediocre for both.

**Hard guardrail:** the CFO surface must be **derived** from data the operator already generates, with **zero additional customer input**. The moment the CFO layer requires anyone to enter data or configure something of its own, it has stopped being a view and become a second product. That is the exact point where "both" turns from leverage into dispersion.

## Explicit prohibitions in the first 90 days

1. No second pricing page and no "Finance" tier.
2. No enterprise AE before month 7.
3. No spend governance module requiring its own configuration.
4. No pilots sold with the CFO as the primary buyer (they may be an approver, not the buyer).
