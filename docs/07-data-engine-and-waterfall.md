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

**What ships, against that.** The accuracy constraint is applied: a provider whose *measured* accuracy is below the declared floor is excluded before the ordering is computed, and a field no provider meets is reported as an excluded floor rather than as a miss — two different answers to a customer, and reporting the second when the first is true sends an operator looking for data that is there (ADR-049). It was not applied at all until D-79: `zolts.waterfall.optimise` took the floor as a keyword argument, and `runtime/enrichment.py` called it without one, so the exclusion this section describes excluded nobody. `E[coverage] ≥ target` is **not** applied and cannot be: the programme schema has no field for a coverage target, so there is nothing to apply. Early stop on SLA (mechanism 4 below) is implicit in the sequential model rather than a separate rule — a later provider is only paid for when every earlier one missed.

**Measured consequence.** `scripts/benchmark_waterfall.py` runs the optimiser against a static premium-first waterfall over 15 geography-and-size cohorts with four providers. Reproduce with `PYTHONPATH=. python3 scripts/benchmark_waterfall.py`.

| Statistic | Saving on cost per verified contact |
|---|---|
| Mean | 51.6% |
| Median | 49.8% |
| Best cohort (`latam_smb`) | 72.4% |
| Worst cohort (`de_ent`) | 29.7% |

Coverage is identical in every cohort — reordering changes cost, never reach — so the entire saving is cost reduction, not a quality trade. That saving is simultaneously the value proposition and the gross margin.

**These are model numbers, not measured provider performance.** Hit rates are plausible, deliberately conservative estimates from public coverage claims. The benchmark validates the algorithm and the mechanism; the magnitude is contingent on real per-cohort hit rates, which only accrue once volume is running. Phase 2 replaces every number in this table with measured ones.

### The structural finding: where the pitch is weakest

The optimiser's edge is **inversely proportional to how good cheap coverage already is in that market**. Where a low-cost provider covers 70%+ (Iberia, LATAM, Poland, Italy), savings exceed 60%. Where it covers barely 40% (DACH, Nordics), the premium provider is genuinely needed and the saving compresses toward 30%.

Commercial consequence, and it is uncomfortable: **the "−X% on data cost" argument is weakest precisely in DACH**, one of the highest-ACV target markets ([01](01-market-and-positioning.md)). In those markets Zolts must lead with governance, jurisdictional policy and incrementality, not with cost. Leading with cost there invites a comparison the product loses.

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

**What ships is smaller than the block above and is a document, not a connector.** `examples/providers/` holds the registrations a tenant can make today: `hunter.yaml` resolves `email` from a first name, a last name and a company domain against the tenant's own Hunter key (decision 38), and `example-enrichment.yaml` is the template for anything else. Limits, DPA metadata and a per-provider circuit breaker are not in the document yet; the runtime's own breaker and rate limiting stand in the way meanwhile.

**A provider's confidence is read on the scale it was given in.** One provider returns `0.92` and another `92` for the same belief. The second was clamped to `1.0` and shown to an operator as certainty (D-47), so a document now declares `confidence_max` beside the path it reads, and a scale with no path to scale is refused.

**A registration answers to one name.** The optimiser plans by the registration's key and the runtime registered the document under the document's name; a row whose two disagreed was planned and then not found (D-48). The CLI refuses the mismatch before the row exists.

**Accuracy is measured, not accepted.** Every email sent returns ground truth (bounce, reply, verification), and that signal retrains the hit-rate matrix. Within six months Zolts knows each provider's real quality per cohort better than the provider does. That dataset is a defensible asset.

## Verification and quality

- Cascading email verification: syntax → MX → SMTP (where acceptable) → verification provider → historical bounce signal.
- **Catch-all addresses** are not discarded; they are marked `risky` and routed to lower-reputation mailboxes or to another channel. Discarding them removes 20-30% of the reachable European TAM.
- Generic role addresses (`info@`, `sales@`) are excluded from 1:1 sequences by default.
- Risk budget: every program declares its bounce tolerance and the router respects that budget.

## Safeguards

| Risk | Control |
|---|---|
| Spend leakage | **Per-tenant ceiling only, today.** `metering.allowance` refuses an action before it happens and the worker defers it, which is real and tested. The per-program and daily ceilings this row claimed do not exist: `spec.budget.monthly_credits` bounds copy generation and nothing else, and `on_exceed` is read by no runtime code at all (D-63). `zolts/controls.py` names every declared control and whether it is honoured; `scripts/validate.py` prints the unenforced ones for each program |
| Provider outage | Circuit breaker plus automatic waterfall reordering |
| Provider price change | Prices are configuration, not code; the optimiser recomputes live |
| Single-provider dependence | At least two providers per critical field before it is considered GA |
| Unlawful data | Every provider declares legal basis and regions; the policy engine blocks invalid combinations before the call is made |
