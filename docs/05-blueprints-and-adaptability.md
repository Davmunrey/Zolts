# 05 — Blueprints: how Zolts adapts to any type of company

## The problem it solves

"Adaptable to any company" is usually implemented badly in one of two ways: (a) an empty generic product that demands six weeks of consulting, or (b) a per-customer fork that destroys margin. Zolts takes a third path: **overlay inheritance** with template extraction.

## Inheritance chain

```
Blueprint (archetype)  →  Industry Pack  →  Tenant  →  Program
      global                 sectoral       customer    play
```

Each level may **add** or **restrict**, never relax policy. Resolution is deterministic and the effective result is inspectable (`zolts explain program X --resolved`). It is the kustomize model applied to GTM: blueprint improvements flow to every customer without breaking their customisations.

## Profiling: 12 dimensions

Onboarding resolves the blueprint from a profile, not from an open interview:

| # | Dimension | Values | What it determines |
|---|---|---|---|
| 1 | Motion | PLG / SLG / PLS / channel / self-serve | Primary signals and tiers |
| 2 | ACV | <1k / 1-10k / 10-50k / 50-250k / >250k | Max cost per account, human-to-agent ratio |
| 3 | Sales cycle | <7d / 1-3m / 3-9m / >9m | Measurement windows and decay |
| 4 | Customer type | B2B / B2C / B2B2C / public sector | Dominant legal basis |
| 5 | ICP breadth | niche (<5k accounts) / mid / mass | Aggressive versus selective enrichment |
| 6 | Geography | EU / UK / US / LATAM / APAC | Policy pack, hours, legal channels |
| 7 | Data maturity | no warehouse / warehouse / lakehouse | Ingestion mode (copy versus zero-copy) |
| 8 | CRM | HubSpot / Salesforce / Attio / Pipedrive / none | Mapper and writeback |
| 9 | Sales team | 0 / 1-5 / 5-25 / >25 | Per-tier capacity, territory routing |
| 10 | Product | software / services / physical / marketplace | Available product signals |
| 11 | Compliance | standard / regulated / public sector | Approvals, retention, residency |
| 12 | Permitted channels | email / LinkedIn / voice / WhatsApp / ads | Available play templates |

The resolver is a **deterministic decision table** (auditable) with LLM-assisted refinement for copy and initial prioritisation only — never for policy.

## The ten output archetypes

| Blueprint | Primary signal | Dominant play | North-star KPI | Compliance |
|---|---|---|---|---|
| `b2b-saas-plg` | Product usage (limits, multi-user) | PLS interception at the friction moment | Free→paid 30d | Contract (own users) |
| `b2b-saas-sales-led` | Funding, hiring, job change | Multithreaded 1:1 across the buying centre | Opportunities created 90d | Legitimate interest |
| `b2b-enterprise` | Category intent, committee, events | Orchestrated ABM, ads plus exec touch | Pipeline per target account | High (DPA, residency) |
| `services-agency` | Job postings, RFPs, agency changes | Consultative outbound plus proof of work | Qualified meetings | Legitimate interest |
| `fintech-regulated` | Licences, funding, regulatory change | Content plus approved consultative sale | Qualified opportunities | Very high (copy approval) |
| `healthtech-lifesci` | Tenders, trials, clinical hiring | Long account, many stakeholders | Accounts activated | Very high (sensitive data) |
| `ecommerce-dtc` | Purchase behaviour, cart, RFM | Winback / replenishment / cross-sell | Net revenue 28d | Consent (B2C) |
| `marketplace-2sided` | Supply/demand imbalance by geo | Acquisition of the scarce side by territory | Liquidity per geo | Mixed |
| `industrial-b2b` | Tenders, plant expansion, imports | Distributor plus long technical sale | Quotes issued | Standard |
| `local-services-multisite` | New site, reviews, ad activity | Phone plus WhatsApp by territory | Contracts signed 60d | Suppression lists |

Each blueprint ships with three to six programs, eight to fifteen configured signals, a pre-calibrated scoring model, a policy pack, a dashboard and a set of declarative tests.

## Extension without forking

| Customer need | Mechanism | No code change |
|---|---|---|
| Own business object (for example "Policy", "Project site") | `tenant_schema` plus `attributes JSONB` | Yes |
| Proprietary data source | Generic connector (REST/SQL/CSV/webhook) via the SDK | Yes |
| Own signal | Declarative `SignalDefinition` | Yes |
| Sector-specific scoring rule | `formula` or a `ScoreModel` trained on their history | Yes |
| Exotic channel (Telegram, own portal) | Channel adapter (SDK, ~200 lines) | Adapter required |
| Specific regulation | Inheritable `Policy` pack | Yes |

## Improvement loop: template extraction

When a customer builds a program that beats the benchmark (lift verified against holdout), the system proposes **anonymising and promoting** it into the blueprint as a template. Effect: the product improves with every customer without any data crossing a tenant boundary. This is the mechanism that turns services into product and avoids the consulting trap.

Rules: promotion only with the tenant's explicit consent, removal of every identifiable literal, and validation across at least three tenants before reaching `stable`.
