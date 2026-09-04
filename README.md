# Zolts — GTM Operating System

**Bottom line:** Zolts is the *execution system* for go-to-market — a composable, warehouse-native, governed layer that turns **signals into decisions, decisions into plays, and plays into revenue measured by incrementality**. It is not another CRM (system of record) and not another AI SDR (commodity). It is the runtime where a company's GTM logic lives: versioned, testable, auditable.

| Dimension | Position |
|---|---|
| Category | GTM Operating System (execution runtime + governance) |
| Core primitive | **GTM Program** = `signal → segment → enrichment → decision → play → channel → measurement` |
| Adaptability | **Blueprints** per company archetype (configuration, never a per-customer fork) |
| Moat | Proprietary outcome loop + data routing layer + jurisdictional policy engine |
| Business model | Platform + seats + credits (three-part tariff), target gross margin 75-80% |

## Plan index

| # | Document | What it settles |
|---|---|---|
| 00 | [Executive summary](docs/00-executive-summary.md) | Thesis, trade-offs, decision |
| 01 | [Market and positioning](docs/01-market-and-positioning.md) | Category, competition, wedge, Build/Buy/Partner |
| 02 | [Architecture](docs/02-architecture.md) | Layers, runtime, stack, ADRs |
| 03 | [Data model](docs/03-data-model.md) | Canonical entities, identity graph, multi-tenancy |
| 04 | [GTM Program DSL](docs/04-gtm-program-dsl.md) | Config-as-code, versioning, testing |
| 05 | [Blueprints and adaptability](docs/05-blueprints-and-adaptability.md) | 10 company archetypes, resolver, overlays |
| 06 | [Signal library](docs/06-signal-library.md) | Catalogue, decay, time-to-touch SLA |
| 07 | [Data engine and waterfall](docs/07-data-engine-and-waterfall.md) | Multi-provider routing, cost optimiser |
| 08 | [AI agent layer](docs/08-ai-agent-layer.md) | Roles, guardrails, evals, auto-send gating |
| 09 | [Execution and deliverability](docs/09-execution-and-deliverability.md) | Channels, sending capacity, reputation |
| 10 | [Measurement and incrementality](docs/10-measurement-and-incrementality.md) | Holdouts by default, per-play P&L |
| 11 | [Compliance and governance](docs/11-compliance-and-governance.md) | GDPR/ePrivacy/AI Act, policy engine |
| 12 | [Pricing and unit economics](docs/12-pricing-and-unit-economics.md) | Tariff, COGS, margins, expansion |
| 13 | [90-day roadmap](docs/13-90-day-roadmap.md) | Three phases with binary exit criteria |
| 14 | [KPIs](docs/14-kpis.md) | Leading/lagging with targets |
| 15 | [Risks and debt](docs/15-risks-and-debt.md) | Commoditisation, mitigations |
| 16 | [Team and operations](docs/16-team-and-operations.md) | Org, burn, forward-deployed model |
| 17 | [Buyer sequencing](docs/17-buyer-sequencing.md) | Operator and CFO: both, in two acts |
| 18 | [Decision register](docs/18-decision-register.md) | 136 decisions with defaults; 12 blocking |

## Technical artefacts

- `examples/programs/*.yaml` — four complete programs (B2B SaaS sales-led, PLG/PLS, ecommerce DTC, local multi-site services)
- `examples/schema/zolts-program.schema.json` — JSON Schema for the DSL
- `examples/sql/schema.sql` — reference DDL for the canonical core
- `scripts/validate.py` — validates programs against the schema (`python3 scripts/validate.py`), enforced in CI

## Definition status

`docs/18` is the living register: **124 decisions carry a default and execute unless contradicted; 12 are blocking** and depend on facts only the founder holds (network, capital, risk appetite). A decision settled elsewhere in these documents is not reopened without amending the register.

## Conventions

See [CLAUDE.md](CLAUDE.md). The repository is English-only — product, code, documentation, commits and pull requests.

## Stated assumptions

This plan assumes: (1) a founding team with senior engineering capability, (2) 6-9 months of initial runway (~€700k-1M), (3) an initial market of Europe plus LATAM with later US expansion, (4) no exclusivity commitment to any data provider. Changing any of these alters phase 1 of the roadmap, not the thesis.
