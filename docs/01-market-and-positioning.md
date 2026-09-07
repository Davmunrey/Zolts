# 01 — Market, competition and positioning

## The real problem (a GTM engineer's diagnosis)

The typical "modern GTM stack" at a 200-person B2B company:

| Layer | Typical tools | Structural failure |
|---|---|---|
| Record | Salesforce / HubSpot / Attio | Does not execute; data arrives late and dirty |
| Data | Apollo, ZoomInfo, Cognism, Clearbit, Crunchbase | Manual waterfall, duplicated credits, no cache |
| Signals | Bombora, G2, RB2B, UserGems, product analytics | Siloed; nobody measures *time-to-touch* |
| Orchestration | Clay, n8n, Zapier, scripts | No versioning, no tests, no rollback, no owner |
| Execution | Smartlead, Instantly, HeyReach, Outreach, ads | Domain reputation managed by feel |
| Measurement | CRM dashboards, HockeyStack/Dreamdata | Correlational attribution, indefensible to finance |

**Hidden cost:** 60-70% of a GTM engineer's time goes to maintaining plumbing rather than designing plays. This is exactly the pattern DevOps solved in infrastructure with IaC and CI/CD.

## Competitive map and where the gap is

| Player | Strength | Blind spot Zolts exploits |
|---|---|---|
| Clay | Enrichment orchestration, community | No versioning, tests or governance; breaks at scale; no incrementality measurement |
| HubSpot / Salesforce (with native AI) | Distribution, system of record | Slow on multichannel execution and jurisdictional policy; lock-in breeds resistance |
| AI SDRs (11x, Artisan, Regie, AiSDR) | Spectacular demo | Black box, no brand control, commoditised, punished by deliverability |
| 6sense / Demandbase | Enterprise intent | Price, model opacity, weak fine-grained execution |
| Unify / Cargo / Default / Octave | Closest to the thesis | Do not yet solve jurisdictional compliance or incrementality as a primitive |
| n8n / Workato / Tray | Flexibility | Generic: no GTM ontology, no signals, no deliverability |

**Defensible gap:** nobody offers all four of (a) config-as-code with tests, (b) a jurisdictional policy engine, (c) native holdouts and incrementality, (d) provider routing with a cost optimiser.

## Positioning

> **Zolts is the GTM runtime.** Your CRM stores what happened. Zolts decides what happens next, executes it under governance, and proves how much of your pipeline is incremental.

Explicit anti-positioning: not a CRM, not a contact database, not an autonomous AI SDR, not a generic automation tool.

## Build / Buy / Partner

| Component | Decision | Structural rationale |
|---|---|---|
| Durable execution runtime | **Build** (a Postgres outbox with leases today, ADR-007; Temporal was the plan) | The core of the moat; competitors cannot replicate it without rewriting their product |
| Program DSL and versioning | **Build** | Defines the ontology; creates legitimate switching cost, not data lock-in |
| Identity graph / resolution | **Build** | Quality here determines everything downstream; impossible to outsource |
| Jurisdictional policy engine | **Build** | Regulatory moat, European advantage, nobody has it |
| Measurement / incrementality | **Build** | Moves the buyer from Marketing to CFO → pricing power |
| Contact and firmographic data | **Buy (multi-provider)** | Commodity; owning it means capex plus GDPR liability |
| Third-party signals (intent, tech, hiring) | **Buy / Partner** | Low marginal cost, no advantage in originating them |
| Email infrastructure (phase 1) | **Partner** (Smartlead/Instantly APIs) | Time to market; move in-house in phase 3 for margin |
| Email infrastructure (phase 3+) | **Build** | Reputable sending capacity becomes the scarce resource |
| Dialer / voice | **Partner** | Mature market, thin margin |
| Ads (Meta/Google/LinkedIn) | **Partner (API)** | Stable APIs, no advantage in rebuilding them |
| CRM | **Never** | Unwinnable war against installed distribution |

## Second-order effects

1. **Channel entropy:** AI agents multiply outbound volume → market-wide reply rates fall → sending reputation and per-signal relevance become the scarce resource. Whoever manages sending capacity as a governed resource, rather than as a box of domains, captures the margin.
2. **Buyer displacement:** measuring incrementality moves the purchase decision from Demand Gen to CFO/RevOps → longer cycle, higher ACV, lower churn, superior pricing power.
3. **Inversion of the data value chain:** if Zolts routes enrichment spend for N customers, providers become commodity suppliers → margin arbitrage and the ability to negotiate wholesale rates.
4. **Regulation as a moat:** tightening GDPR and AI Act enforcement pushes "spray and pray" competitors out of the European enterprise market; the policy engine appreciates in value with every published fine.
