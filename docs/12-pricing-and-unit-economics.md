# 12 — Pricing and unit economics

## Structure: three-part tariff

| Component | What it captures | Why |
|---|---|---|
| **Platform** (monthly) | Access to the runtime, blueprints, policies, measurement | Predictable revenue, ~92% margin |
| **Seats** for operators | Human usage (editors and operators; viewers free) | Scales with the team without taxing visibility |
| **Credits** (usage) | Enrichment, AI, sends, agent runs | Aligns price with value and covers variable COGS |

Rejected as the primary model: **price per meeting generated**. It creates attribution disputes, adverse selection (customers with the weakest product consume the most) and misaligns the incentive toward volume. It is offered as a performance add-on on holdout-verified lift, Enterprise only.

## Plans

| Plan | Platform/month | Seats included | Credits/month | Profile |
|---|---|---|---|---|
| **Starter** | €490 | 2 | 15,000 | One or two programs, single motion, no SSO |
| **Growth** | €1,490 | 5 | 60,000 | Multi-program, all channels, full blueprints |
| **Scale** | €3,900 | 12 | 200,000 | Time-to-touch SLA, SSO, EU residency, sandbox |
| **Enterprise** | from €8,000 | Custom | Custom | Isolation, bespoke DPA, own policy packs, GTM engineer support |

Additional seat: €90/month. Additional credits: volume-tiered (below). Implementation services: €5-15k one-off, **capped at 20% of total revenue** (above that, the multiple collapses).

## Credits: definition and COGS

1 credit ≈ €0.01. Volume pricing: 0-100k → €0.010; 100k-500k → €0.008; >500k → €0.006.

| Action | Credits | Estimated COGS | Gross margin |
|---|---|---|---|
| Verified email enrichment | 8 | €0.025-0.04 | ~55% |
| Mobile phone | 25 | €0.10-0.15 | ~45% |
| Account firmographics | 4 | €0.01-0.02 | ~60% |
| Signal check (per account per day) | 0.5 | €0.001-0.004 | ~50% |
| Message generation (AI) | 3 | €0.004-0.012 | ~70% |
| Research dossier (agent) | 20 | €0.05-0.09 | ~65% |
| Email send | 1 | €0.001-0.003 | ~80% |
| Program execution (step) | 0.2 | ~€0.0005 | ~85% |

**Target gross margin:** 60-65% in year one (credits dominate), 75-80% by year three (mix shifted toward platform, plus wholesale data rates and in-house sending infrastructure).

Key lever: the waterfall optimiser ([07](07-data-engine-and-waterfall.md)) improves credit margin **without raising price**. Every point of efficiency goes straight to margin or to competitiveness, at will.

## Target unit economics

| Metric | Year 1 | Year 2 | Year 3 |
|---|---|---|---|
| Average ACV | €14k | €26k | €42k |
| Gross margin | 62% | 71% | 78% |
| CAC (blended) | €9k | €16k | €24k |
| CAC payback | 12 mo | 10 mo | 8 mo |
| NRR | 105% | 118% | 130% |
| Gross logo churn | 22% | 15% | 10% |
| LTV/CAC | 2.1× | 3.4× | 4.8× |

Expansion engine, in expected order of NRR contribution: credit consumption → seats → additional programs → new blueprints (new business units at the same customer) → modules (advanced agents, residency, isolation).

## Act 2: spend governance contract (month 9+)

Once baseline, measured lift and real cost per meeting have accumulated, the existing account converts from a consumption contract to a **platform contract with spend governance**, anchored to a percentage of GTM spend under management (reference: 3-6%), not to the cost of the tool it replaced. Target ACV: 3-5× that of Act 1.

Non-negotiable activation condition: the CFO view must be derived from data the operator already generates, with zero additional customer input. The moment it requires its own configuration, it is a second product. Detail in [17](17-buyer-sequencing.md).

## Discount guardrail

Maximum AE discount: 10%. 10-20% requires a director. Above 20% only against a multi-year commitment with upfront payment. Hard rule: **never discount the platform; discount credits** — elastic and variable-cost — which preserves the price anchor.

## Where pricing power comes from

1. **Incrementality measurement**: if Zolts proves X euros of incremental pipeline, the price anchors to a share of X, not to the cost of the displaced tool.
2. **Legitimate switching cost**: the customer's versioned GTM logic lives in Zolts. It is not data lock-in (data is exportable), it is accumulated operational knowledge — far more defensible and far less resented.
3. **Compliance**: replacing a system the DPO has approved carries an organisational cost nobody absorbs for a 15% discount.
4. **Consolidation**: every tool removed from the stack (typically five to eight, at €3-9k per month) is freed budget that justifies the price.
