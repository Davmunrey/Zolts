# 15 — Risks, debt and mitigations

## Risk matrix (ordered by expected damage)

| # | Risk | Prob. | Impact | Structural mitigation | Early signal |
|---|---|---|---|---|---|
| 1 | **Commoditisation by incumbents** (HubSpot/Salesforce bundle the same) | High | High | Do not compete on record; the value is runtime plus policy plus incrementality, areas where incumbents are slow by architecture and by conflict with their installed base | "AI agents" announcements native to their roadmap |
| 2 | **Clay or Unify move onto the same ground** | High | High | A 12-18 month lead in jurisdictional compliance and experimentation; both require a core rewrite, not a feature | Holdouts or policy packs appearing in their product |
| 3 | **Collapse of email channel deliverability** | Medium | Very high | Multichannel from day one; sending capacity as managed inventory; growing weight of product and inbound signals | Sustained fall in reply rate across all tenants |
| 4 | **Data provider shock** (price, policy, API shutdown) | Medium | High | Per-field abstraction, at least two providers per critical field, prices as configuration, contractual pass-through to the customer | Terms or pricing change from a provider holding over 25% share |
| 5 | **GDPR complaint or fine against a customer over routed data** | Low-Medium | Very high | Mandatory per-action legal basis, minimisation, signed DPAs, full audit, conservative default packs (block before permit) | Sector-wide complaints against enrichment providers |
| 6 | **Brand incident from AI content** (a false claim to an enterprise account) | Medium | High | Provenance verification, eval gating, tier 1 always human, approved claim library | Factuality eval dropping after a model change |
| 7 | **The services trap** (every customer demands bespoke implementation) | High | Medium-High | Blueprints and overlays; hard 20% cap on services revenue; template extraction | Engineering hours per customer rising month over month |
| 8 | **Design partner concentration** | High | Medium | Maximum 25% of ARR per customer from month 9; diversify archetypes from month 4 | One customer above 30% of ARR |
| 9 | **Sales cycle longer than modelled** (CFO buyer) | Medium | Medium | Land with self-serve Growth and expand; do not depend on enterprise to survive | Median cycle over 75 days in mid-market |
| 10 | **LinkedIn or another platform closes automated access** | Medium | Medium | Never depend on unsupported automation in critical paths; the channel is substitutable in the DSL | ToS changes or waves of restrictions |

## Technical debt: what is accepted and what is not

| Debt | Acceptable? | Condition |
|---|---|---|
| Heuristic scoring before a trained model | Yes | A stable `ScoreModel` interface from day one so it can be swapped without touching programs |
| Email via partner instead of in-house infrastructure | Yes | Channel abstraction from day one; migration planned, not improvised |
| A single complete blueprint in phase 1 | Yes | The overlay mechanism must exist even with only one blueprint |
| Deterministic-only identity graph at the start | Yes | The schema must support probabilistic confidence from the beginning |
| **Skipping idempotency** | **No** | A duplicate send is an unrecoverable trust failure |
| **Skipping the policy decision log** | **No** | Without audit there is no enterprise, and it cannot be recovered retroactively |
| **Unversioned programs** | **No** | It is the product's reason to exist |
| **Optional evals for auto-send** | **No** | One brand incident costs more than the entire first year of ARR |

## Organisational debt

| Pattern | Consequence | Countermeasure |
|---|---|---|
| Engineering answering customer tickets | Roadmap hijacked | A dedicated Forward-Deployed GTM Engineer, with a bounded time budget and an obligation to convert every intervention into reusable configuration |
| Founder as sole seller past month 9 | Growth ceiling | Hire AE #1 in month 7 with a documented, validated playbook |
| No owner for data quality | The identity graph degrades silently | A named owner plus quality metrics on the weekly internal dashboard |
| Unrecorded architecture decisions | Endless reopening of settled debates | Mandatory ADRs (see [02](02-architecture.md)) |

## Commoditisation risk: the honest question

If in 24 months anyone can build "an agent that enriches and sends emails" with three LLM calls, what is left of Zolts?

What is left is what a prompt cannot copy:
1. **The outcome dataset** — which signal, in which segment, with which message, produced which result, under experimental control. It accrues only through execution, and it cannot be bought.
2. **The real per-provider, per-cohort hit-rate matrix** — built only with real volume and ground truth.
3. **Sending reputation** — physical, months long, non-transferable.
4. **Policy packs validated by DPOs** — adoption cost and legal responsibility nobody gives away.
5. **Each customer's versioned GTM logic** — their own operational knowledge, accumulated in the system.

Strategic corollary: **real volume must be executed as early as possible**, even at thin margins at first. The moat accrues through execution, not through architecture. Every month of delay in having customers executing is a month of moat not built.
