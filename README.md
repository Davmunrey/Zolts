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
| 19 | [Reference core](docs/19-reference-core.md) | Which claims are proven, and what building it changed |
| 20 | [The runtime](docs/20-runtime.md) | What actually runs, how a signal becomes a gated action, what breaks first |

## Technical artefacts

### The runtime — the part that runs

- `runtime/migrations/` — 18 tables on Postgres 16, row-level security forced on all 16 tenant-scoped ones
- `runtime/db.py`, `runtime/repo/` — data access where the only way to get a cursor is to name a tenant
- `runtime/engine/` — signal ingest, holdout assignment, step planning, the policy gate, and a leased worker with backoff and dead-letter
- `runtime/connectors/` — the CRM contract and its suite, HubSpot and Pipedrive as sources, a generic source driven by a tenant-authored mapping for CRMs nobody here has seen, Smartlead for sending
- `runtime/engine/inbound.py`, `runtime/api/webhooks.py` — signed inbound webhooks that close the measurement loop: replies and deals become outcomes, bounces and opt-outs become suppressions
- `runtime/agents/`, `runtime/engine/generate.py` — the agent layer: agents write proposals and cannot send; cost is governed by [Trazum](https://github.com/Davmunrey/Trazum)'s `spend_guard` before every model call
- `runtime/api/` — HTTP surface with API-key tenancy; no route takes a tenant id, and `GET /console` serves the operator surface with the tenant's live figures inlined
- `runtime/cli.py` — migrate, provision a tenant, issue a key, seal a credential, run the worker, serve
- `Dockerfile`, `docker-compose.yml`, `render.yaml`, `fly.toml` — database, migrations, API and worker in one command, locally or on a managed host

```bash
export ZOLTS_SECRET_KEY=$(openssl rand -hex 32)
docker compose up                                      # database, API, worker
python3 -m runtime.cli quickstart --slug acme          # tenant, programs, key, console URL
PYTHONPATH=. python3 scripts/smoke_runtime.py          # signal in, gated action out, outcome back
```

### The reference core — the part that decides

- `zolts/` — reference implementation of the core primitives: overlay resolution, deterministic holdouts, signal decay and PIT-R scoring, the waterfall cost optimiser, the policy engine, and the DSL loader and linter
- `zolts/blueprint.py` plus `blueprints/` — the archetype resolver and 11 blueprints as configuration
- `zolts/catalog.py` — the join between programs and blueprints, and the integrity checks neither schema can perform
- `zolts/deliverability.py` — sending capacity as managed inventory: warm-up, thresholds, per-provider segregation, staggered ramp
- `zolts/provenance.py` — every claim in a generated message maps to a source, or it is removed
- `zolts/evals.py` — the auto-send gate: compliance vetoes, an unmeasured check is not a pass
- `tests/` — 539 tests, each backing a specific claim made in `docs/`; the 186 runtime tests run against a real Postgres and CI fails a run that skipped them
- `examples/tests/*.test.yaml` — declarative program tests: compliance expectations enforced in CI
- `examples/programs/*.yaml` — four complete programs (B2B SaaS sales-led, PLG/PLS, ecommerce DTC, local multi-site services)
- `examples/schema/zolts-program.schema.json` — JSON Schema for the DSL
- `examples/sql/schema.sql` — the original reference DDL, superseded for execution by `runtime/migrations/`
- `scripts/validate.py` — validates programs against the schema
- `scripts/benchmark_waterfall.py` — measures optimiser savings against a static waterfall

```bash
python3 scripts/validate.py                            # schema validation
PYTHONPATH=. python3 -m pytest tests/ -q               # 539 tests
PYTHONPATH=. python3 scripts/run_program_tests.py      # 20 declarative cases
PYTHONPATH=. python3 scripts/benchmark_waterfall.py    # measured savings
```

The reference core exists to test the plan; the runtime exists to run it. Building both has corrected thirteen defects so far. Ten came from the core: five in the example programs, one over-generalised product invariant, one overlay bug that would have voided the compliance guarantee, and one roadmap exit criterion that was unmeasurable as written — four of them the same failure in different clothing, an opt-out path that silently did not work. Three came from the runtime: a schema named after a database role, which made a second migration run duplicate every table and report success; a migration runner whose version ledger rolled back while its DDL committed; and a connector branch that could never execute because the helper raised on the status it was meant to inspect. See [19](docs/19-reference-core.md) and [20](docs/20-runtime.md).

Two more came from rendering the console against live data: the surface crashed on a program with no measurement yet — which is every program on day one — and it reported EUR 1.75m of incremental pipeline against a control arm with zero observed conversions, because its detectable effect had been computed from a floor rather than an estimate. That second one is now a rule in the core: no effect is declared while either arm carries fewer than five observed conversions.

Wiring the agent layer to real data added two more: the provenance rule measured overlap across every content word, so an interpretive clause could sink a sentence whose exact fact was in evidence; and the retrieval split a company's name into its own citation, so no single source could support a sentence that named who did the thing.

Every one of the seventeen was found by executing, none by reading.

## Definition status

`docs/18` is the living register: **124 decisions carry a default and execute unless contradicted; 12 are blocking** and depend on facts only the founder holds (network, capital, risk appetite). A decision settled elsewhere in these documents is not reopened without amending the register.

## Design

- [`DESIGN.md`](DESIGN.md) — the design system, in the [DESIGN.md](https://github.com/VoltAgent/awesome-design-md) format that design agents read directly.
- `design/console.html` — the reference product surface implementing it: a program's evidence, P&L and policy trace.

Its governing rule is that **the violet accent never enters a data region**. Brand and interaction live in chrome; five separate colours carry measurement semantics — verified lift, experimental control, policy denial, review, live latency. Violet is the only hue left once measurement has taken its five, so brand and evidence can never collide on a dense screen.

That is also positioning. The category's most visible product renders warm cream, claymation illustration and five saturated card colours; reading as its opposite communicates *audited* before a word is read.

## Deployment

The product surface deploys as a static site with no build step beyond a wrapper.

```bash
PYTHONPATH=. python3 scripts/build_site.py   # derives the data, inlines it, derives the CSP
```

The surface carries no data of its own. `scripts/build_fixture.py` reads the real programs and blueprints and computes every figure with the same `zolts.experiment` and `zolts.policy` functions the test suite covers, so the minimum detectable effect on screen cannot drift from the one in the runtime. Observed rates are the only invented numbers and are marked as such; everything downstream of them — lift, significance, whether a pipeline figure may be reported at all — is derived.

`scripts/build_site.py` hashes the inline style and script it actually ships into the Content-Security-Policy, so the policy cannot drift from the page. `style-src-elem` stays hash-locked while `style-src-attr` allows inline attributes, because the surface sets transforms from data at runtime and a hash never covers a `style=""` attribute. CI fails if the committed `site/` is stale.

| Target | Configuration | Status |
|---|---|---|
| Cloudflare Pages | `wrangler.toml`, `site/_headers`, `.github/workflows/deploy-pages.yml` | Deploys on push to `main` once `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` are set; the job skips cleanly until then |
| Vercel | `vercel.json` | Connect the repository; output directory `site` |

Manual Cloudflare deploy from a checkout:

```bash
npx wrangler pages deploy site --project-name=zolts
```

## Conventions

See [CLAUDE.md](CLAUDE.md). The repository is English-only — product, code, documentation, commits and pull requests.

## Stated assumptions

This plan assumes: (1) a founding team with senior engineering capability, (2) 6-9 months of initial runway (~€700k-1M), (3) an initial market of Europe plus LATAM with later US expansion, (4) no exclusivity commitment to any data provider. Changing any of these alters phase 1 of the roadmap, not the thesis.
