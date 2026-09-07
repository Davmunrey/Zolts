# 16 — Team, cost and operations

## Minimum viable team (90 days)

| Role | FTE | Responsibility | Loaded cost/month (Europe) |
|---|---|---|---|
| Founding engineer — backend/runtime | 1.0 | Durable runtime (the outbox, ADR-007), DSL, idempotency, policy engine | €9-11k |
| Data/platform engineer | 1.0 | Ingestion, identity graph, waterfall router, warehouse | €8-10k |
| Full-stack engineer | 1.0 | Studio, CLI, onboarding, billing | €7-9k |
| AI/ML engineer | 0.5-1.0 | Agents, evals, scoring | €9-12k |
| **Forward-Deployed GTM Engineer** | 1.0 | Onboards design partners and **turns every onboarding into a blueprint** | €6-8k |
| Founder/CEO | 1.0 | Sales, design partners, product | — |
| Design (fractional) | 0.3 | Studio and dashboards | €3k |
| Legal/DPO (fractional) | 0.2 | Policy packs, DPAs | €3-4k |

**Burn:** €48-62k/month. **Per quarter:** roughly €150-185k plus €15-25k of infrastructure, data and tooling. With €15k of pilots collected, net quarterly burn is approximately €155-195k.

The critical and least obvious role is the **Forward-Deployed GTM Engineer**: simultaneously the engine of early customer success and the mechanism that prevents the services trap, because their deliverable is not "a happy customer" but "the blueprint that makes the next customer not need me".

## Operating rhythm

| Cadence | Forum | Decision produced |
|---|---|---|
| Daily | 15-minute standup | Unblocking |
| Weekly | Product metrics review (latency, cost per contact, evals, deliverability) | Sprint adjustment |
| Weekly | Pipeline and per-design-partner review | Commercial risk |
| Biweekly | Customer program lift review | What gets promoted to a blueprint |
| Monthly | Architecture review, ADRs, debt | Which debt gets paid |
| Quarterly | Phase exit criteria | Advance or cut scope |

## Later hiring (months 4-12, in order)

1. AE #1 (month 7) — only with a founder-validated playbook.
2. Connector engineer (month 5) — once maintenance exceeds 20% of engineering time.
3. Deliverability / sending infrastructure engineer (month 8) — when sending is brought in-house.
4. Data scientist (month 9) — when volume supports per-tenant models.
5. Second Forward-Deployed GTM Engineer (month 6) — the onboarding bottleneck arrives before the sales bottleneck.

## Engineering principles

1. **Nothing reaches production without a trace, an attributed cost and a recorded policy decision.**
2. **Connectors pass contract tests or they are not merged.**
3. **No prompt or model change ships without passing the golden set in CI.**
4. **Anything a customer asks for "bespoke" is implemented as configuration or not at all.**
5. **If the lift cannot be measured, the program does not launch.**
