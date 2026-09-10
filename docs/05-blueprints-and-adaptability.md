# 05 — Blueprints: how Zolts adapts to any type of company

## The problem it solves

"Adaptable to any company" is usually implemented badly in one of two ways: (a) an empty generic product that demands six weeks of consulting, or (b) a per-customer fork that destroys margin. Zolts takes a third path: **overlay inheritance** with template extraction.

## Inheritance chain

```
Blueprint (archetype)  →  Industry Pack  →  Tenant  →  Program
      global                 sectoral       customer    play
```

Each level may **add** or **restrict**, never relax policy. It is the kustomize model applied to GTM: blueprint improvements flow to every customer without breaking their customisations.

**Two of the four layers are built, and the resolver had no caller until D-94.** `zolts/overlay.py` enforced restrict-only semantics, was tested in isolation, and nothing in the runtime invoked it — so a programme could publish three touches a week under an archetype permitting one, and the compliance tier a customer chose meant nothing. It now runs at the publish choke point against the **tenant's** archetype (ADR-057), which is the same layer the discount authority reads (ADR-052).

| Layer | Status |
|---|---|
| Blueprint | **Built.** `blueprints/*.yaml`, resolved from the profile below |
| Industry pack | **Not built.** No sectoral layer exists between the archetype and the tenant |
| Tenant | **Not built** as a policy layer. A tenant names its archetype and its programmes; it declares no policy of its own. Every shipped programme says `inherit: tenant_default`, naming a layer that is not there |
| Program | **Built.** `spec.policy.overrides`, refused at publish if it loosens the archetype |

`zolts explain program X --resolved` is **not a command that exists**; the resolution a reader would want to inspect is the refusal message, which names the key, both values and the layer that raised it. A real `explain` waits on the two missing layers, because resolving a chain of two and calling it four is the defect above in a new coat.

## Profiling: 13 dimensions

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
| 13 | Sector | software / financial services / healthcare / manufacturing / public / retail / … | Separates archetypes whose policy packs differ but whose other twelve answers coincide |

**Dimension 13 was added under test, not designed in.** With twelve, a regulated fintech and a regulated life-sciences company produced identical profiles: same motion, ACV band, cycle length, compliance tier, CRM and team size. Their canonical profiles scored exactly equal, and one of them resolved to the generic enterprise archetype. Twelve dimensions could not distinguish two archetypes whose legal and approval requirements differ materially. Sector is deliberately coarse — it exists to separate policy packs, not to be a taxonomy.

The resolver is a **deterministic decision table** (auditable) with LLM-assisted refinement for copy and initial prioritisation only — never for policy.

Implemented in `zolts/blueprint.py`, with the archetypes as configuration in `blueprints/` and a JSON Schema in `examples/schema/zolts-blueprint.schema.json`. Two dimensions are **hard**: a mismatch on `customer_type` or `compliance_tier` disqualifies an archetype outright rather than merely costing it points, because getting either wrong means the wrong legal basis and the wrong policy pack — a compliance failure, not a poor fit. Ties break on blueprint key alphabetically: an arbitrary but stable rule beats a subtle one, because the same profile must always land on the same policy pack. A profile matching nothing on the hard dimensions still resolves, flagged for human review, rather than being turned away at onboarding.

## The eleven output archetypes

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
| `public-sector` | Published tenders, budget allocation, framework renewal | Tender response within the published window | Tenders submitted | Public sector |

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
