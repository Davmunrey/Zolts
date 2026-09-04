# 07 — Data engine: waterfall router and enrichment economics

## Principle

The user **never picks a provider**. They declare the result they need:

```yaml
enrich:
  person:
    require: [work_email, phone_mobile]
    accuracy_sla: 0.95
    max_cost_per_contact: 0.60
```

The router decides which providers to query, in what order, and when to stop. This is what makes Zolts the arbitrage layer of the B2B data market.

## Expected-cost optimiser

For a field `f` with orderable providers `p1..pn`, each with conditional hit rate `h_i` (given that the previous ones missed), cost `c_i` and accuracy `a_i`:

```
E[cost]     = Σ_i  c_i × Π_{j<i} (1 - h_j)
E[coverage] = 1 - Π_i (1 - h_i)
```

The router solves for an ordering that **minimises E[cost] subject to E[coverage] ≥ target and weighted accuracy ≥ accuracy_sla**. Because `h_i` depends on the segment (country, size, sector, seniority), a hit-rate matrix is maintained per *cohort* and updated on every call.

**Economic consequence:** in cohorts where a cheap provider hits 70% of the time, this saves roughly 40-60% against the static waterfall most teams configure by hand. That saving is simultaneously the value proposition and the gross margin.

## Savings layers (in order of impact)

| # | Mechanism | Typical saving | Note |
|---|---|---|---|
| 1 | **Do not enrich what will not be used** | 30-50% | Enrich *after* fit scoring, never before. The most expensive mistake in the sector. |
| 2 | Per-tenant cache with per-field TTL | 15-25% | Email 180d, phone 365d, firmographics 90d, technographics 45d |
| 3 | Per-cohort hit-rate matrix | 20-40% | Orders the waterfall dynamically |
| 4 | Early stop on SLA | 10-15% | Do not call the fourth provider once the required accuracy is met |
| 5 | Anonymised global statistics | 5-15% | Aggregate hit rates across tenants; **never data values** |
| 6 | Wholesale negotiation | 20-40% of COGS | A consequence of aggregating volume: active from roughly 50 customers |

Layer 5 is delicate: what is shared are *provider performance metrics*, never records. The boundary is encoded in the policy engine and audited.

## Provider contract

```yaml
kind: Connector
metadata: {key: provider_x}
spec:
  fields: [work_email, phone_mobile, linkedin_urn]
  limits: {rps: 10, daily: 50000, burst: 50}
  pricing: {model: per_hit, unit_cost_eur: 0.021, minimum_commit_eur: 500}
  quality: {measured: true}       # accuracy verified, not vendor-declared
  legal: {dpa: signed, subprocessor: true, regions: [eu, us], basis_supported: [legitimate_interest]}
  failure_policy: {timeout_ms: 4000, retries: 2, circuit_breaker: 5xx_rate>0.2/60s}
```

**Accuracy is measured, not accepted.** Every email sent returns ground truth (bounce, reply, verification), and that signal retrains the hit-rate matrix. Within six months Zolts knows each provider's real quality per cohort better than the provider does. That dataset is a defensible asset.

## Verification and quality

- Cascading email verification: syntax → MX → SMTP (where acceptable) → verification provider → historical bounce signal.
- **Catch-all addresses** are not discarded; they are marked `risky` and routed to lower-reputation mailboxes or to another channel. Discarding them removes 20-30% of the reachable European TAM.
- Generic role addresses (`info@`, `sales@`) are excluded from 1:1 sequences by default.
- Risk budget: every program declares its bounce tolerance and the router respects that budget.

## Safeguards

| Risk | Control |
|---|---|
| Spend leakage | Per-program, per-tenant and daily ceilings; `on_exceed: pause_and_alert` |
| Provider outage | Circuit breaker plus automatic waterfall reordering |
| Provider price change | Prices are configuration, not code; the optimiser recomputes live |
| Single-provider dependence | At least two providers per critical field before it is considered GA |
| Unlawful data | Every provider declares legal basis and regions; the policy engine blocks invalid combinations before the call is made |
