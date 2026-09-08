# 14 — KPIs

## Product and execution

| Metric | Type | 90-day target | 12-month target |
|---|---|---|---|
| Time-to-first-program (new tenant) | Leading | <60 min | <20 min |
| p95 signal-to-executed-action latency (tier A) | Leading | <60 min | <15 min |
| Active programs per tenant | Leading | 3 | 8 |
| Share of actions with a recorded policy decision | Leading | 100% | 100% |
| Cost per verified contact (median cohort) | Leading | −30% versus static waterfall | −50% |
| Cost per verified contact (worst cohort) | Leading | −20% floor | −30% floor |
| Email bounce rate | Leading | <2% | <1.2% |
| Spam complaint rate | Leading | <0.1% | <0.05% |
| Positive reply rate | Leading | >3% | >6% |
| Share of claims with verified provenance | Leading | >90% | >98% |
| Share of tier 2/3 touches auto-sent | Leading | 60% | 85% |
| Tenants with a baseline captured within 7 days | Leading | 100% | 100% |
| Accounts with accumulated significant lift (Act 2 eligible) — tenants whose latest frozen incrementality report says `significant` (ADR-043) | Leading | — | >60% |
| Brand incidents from AI content | Lagging | 0 | 0 |
| Average incremental lift versus holdout | Lagging | >1.5× | >2.5× |
| Runtime uptime | Lagging | 99.5% | 99.9% |

## Business

| Metric | Type | 90-day target | 12-month target |
|---|---|---|---|
| Paying design partners | Leading | 3 | — |
| Paying customers | Lagging | 10 | 45 |
| ARR | Lagging | €180-250k | €1.1-1.5M |
| Average ACV | Lagging | €14k | €20k |
| Pilot-to-annual conversion | Leading | >66% | >75% |
| Gross margin | Lagging | >55% | >68% |
| NRR | Lagging | — | >115% |
| CAC payback | Lagging | — | <12 mo |
| Median implementation days | Leading | <5 | <2 |
| Share of revenue from services | Leading | <25% | <15% |
| Engineering dedicated to a single customer | Leading | <20% | <5% |

## Single health metric (north star)

**Holdout-verified incremental pipeline generated through Zolts, per month.**

Rationale: it is the only metric that cannot be inflated with activity. Sending more emails does not move it; only executing the right play at the right moment does. It aligns product, engineering and sales behind the same objective, and it is exactly the number the customer takes to their board.

## Counter-metrics (watched to prevent perverse optimisation)

| If this rises… | …something is wrong |
|---|---|
| Send volume per customer without a rise in lift | We are selling activity, not outcome |
| Credits consumed without a rise in incremental pipeline | The router or the scoring is miscalibrated |
| Services revenue | The product is not genuinely adaptable |
| Programs created but never published | Friction in `plan`/`test`; the UI is not usable |
| Connector support tickets | Connector debt is overflowing |
