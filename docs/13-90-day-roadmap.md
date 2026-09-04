# 13 — 90-day roadmap

Quarter objective: **three paying design partners running real programs in production, with lift measured against holdout, and the blueprint engine proving that the fourth customer can be onboarded without engineering.**

Governance rule: every phase has binary exit criteria. No phase advances with criteria unmet; scope is cut, never quality.

---

## Phase 1 · Days 1-30 — Executable core and design partners

**Objective:** one complete program, end to end, in production, with a real paying customer.

| Workstream | Deliverable | Exit criterion |
|---|---|---|
| Data | Canonical schema, RLS, identity graph v0 (deterministic) | 1M accounts resolved with under 2% collisions in an audited sample |
| Ingestion | HubSpot, Salesforce and Postgres/Snowflake connectors (read plus writeback) | Bidirectional CDC stable for seven days without intervention |
| Runtime | Temporal, DSL v0.1 (`trigger/audience/enrich/score/route/plays/exit`), idempotency | Replay of 10k enrollments with zero duplicate sends |
| Signals | Five tier A/B signals: funding, hiring, job change, pricing-page visit, tech install | p95 signal-to-proposed-action under 10 minutes on tier A |
| Execution | Email via partner API plus CRM tasks | 3k emails sent, bounce under 2% |
| Measurement | Deterministic holdout plus outcome table | Reproducible assignment verified by test |
| Compliance | Policy engine v0 (ES, FR, UK, US), suppression, quiet hours | 100% of actions carry a recorded `policy_decision` |
| Baseline | Capture of prior GTM spend and 90-day conversion rates, frozen and signed per tenant | 100% of tenants with a baseline captured within seven days |
| Commercial | Three design partners signed, paid pilots at €5k for three months | €15k collected; commitment letter with success criteria |

**Irreversible requirement:** a baseline cannot be reconstructed retroactively. Without it, the month-9 CFO expansion ([17](17-buyer-sequencing.md)) slips a full cycle. Cost of capturing it now: roughly 10-15% of engineering. Cost of skipping it: six to nine months.

**Phase risk:** building product without a customer. Mitigation: day one starts by selling the pilots, not by architecting. No component enters the plan unless a signed pilot needs it.

---

## Phase 2 · Days 31-60 — Differentiation and margin

**Objective:** turn the executor into a platform: economic routing, governed agents, sellable measurement.

| Workstream | Deliverable | Exit criterion |
|---|---|---|
| Waterfall router | Per-field abstraction, three or more providers, per-cohort hit-rate matrix, optimiser | Median cohort saving ≥30% on cost per verified contact versus a static waterfall, **and no cohort below 20%**. Stated per cohort because the model shows DACH compressing to ~30% while Iberia and LATAM exceed 60% — a single blended number would hide the market where the pitch is weakest |
| Agents | Researcher, Copywriter and Qualifier with provenance verification and an eval harness | 90%+ of claims carry provenance; 200-case golden set in CI |
| Deliverability | Domain and mailbox management, warm-up curves, Postmaster, circuit breakers | Zero burned domains; complaints under 0.1% across all pilots |
| Channels | LinkedIn, ads (audiences), voice tasks | Two multichannel programs in production |
| Measurement | Per-play P&L dashboard plus MDE and power calculation | Every pilot receives a lift report with a confidence interval |
| Signals | Catalogue at 15 signals plus declarative `SignalDefinition` | A customer adds their own signal with no Zolts engineering |
| Blueprints | Three complete blueprints (`b2b-saas-sales-led`, `b2b-saas-plg`, `services-agency`) | A new tenant configured and executing within five working days |
| Product | Studio v1 (bidirectional DSL↔UI editor) plus CLI | A non-technical operator publishes a program unassisted |

**Phase risk:** dispersion across agents, channels and routing. Mitigation: if something must be cut, cut channels (the partner covers the gap); the router and the evals are moat core and are not reduced.

---

## Phase 3 · Days 61-90 — Commercial scalability

**Objective:** prove that customers four through ten consume no engineering, and build the revenue machine.

| Workstream | Deliverable | Exit criterion |
|---|---|---|
| Onboarding | 12-dimension profiling → blueprint resolver → first program | Time-to-first-program under 60 minutes, self-serve, on Starter |
| Monetisation | Credit metering, billing (Stripe), ceilings and spend alerts | Correct automated billing across two consecutive cycles; zero disputes |
| Marketplace | Program import/export, anonymised template extraction | Five templates promoted from real customer usage |
| Compliance | Full GDPR pack (DPA, RoPA, automated DSAR, sub-processors) plus SOC 2 Type I kickoff | An enterprise pilot's DPO approves in writing |
| Scale | EU residency, SSO/SAML, full RBAC, per-tenant limits | One Scale or Enterprise contract signed |
| Agents | Auto-send gating by eval score on tiers 2 and 3 | 60%+ of tier 2 touches auto-sent with no brand incidents |
| Commercial | Ten paying customers; Zolts's own outbound running on Zolts | €180-250k ARR run rate; two or more customers from own outbound |

**Mandatory dogfooding:** Zolts's own GTM runs entirely on Zolts from day 45. It is simultaneously quality control, a live demo and a source of templates.

---

## Out of scope for 90 days (explicit decision)

A second tariff or CFO-facing surface (see the prohibitions in [17](17-buyer-sequencing.md)), in-house email infrastructure, a native dialer, per-tenant trained scoring models (calibrated heuristics are used instead), blueprints four through ten, ISO 27001, a public third-party marketplace, and a mobile app. Documented to avoid renegotiating scope every sprint.
