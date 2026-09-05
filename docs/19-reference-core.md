# 19 — Reference core: which claims are proven

## What this is

`zolts/` is a dependency-light implementation of the primitives the plan asserts, with `tests/` proving them. It is **not the production runtime**. Its job is to answer one question honestly: *do the numbers and invariants in these documents actually hold?*

Building it changed the plan in four places. That is the point of building it.

```bash
python3 scripts/validate.py                            # schema validation
PYTHONPATH=. python3 -m pytest tests/ -q               # 184 tests
PYTHONPATH=. python3 scripts/run_program_tests.py      # 20 declarative cases
PYTHONPATH=. python3 scripts/benchmark_waterfall.py    # measured savings
```

## Claim coverage

| Claim | Where asserted | Module | Status |
|---|---|---|---|
| Holdout assignment is stable across processes and restarts | [10](10-measurement-and-incrementality.md) | `experiment.py` | **Proven.** SHA-256, not `hash()`, which is per-process salted |
| 5% holdouts are resolvable without quantisation error | [10](10-measurement-and-incrementality.md) | `experiment.py` | **Proven.** 10,000 buckets; 100 would distort a 5% split |
| Programs randomise independently | [10](10-measurement-and-incrementality.md) | `experiment.py` | **Proven.** Two 20% holdouts co-occur at 4%, not 20% |
| A program cannot reach significance below a sample floor | [10](10-measurement-and-incrementality.md) | `experiment.py` | **Proven.** 500/arm cannot resolve a 2-point lift on a 5% baseline |
| Greedy cost/hit ordering is optimal | [07](07-data-engine-and-waterfall.md) | `waterfall.py` | **Proven** against exhaustive permutation, not cited |
| Reordering never costs coverage | [07](07-data-engine-and-waterfall.md) | `waterfall.py` | **Proven.** Identical reach, cost-only difference |
| 30-50% saving on cost per verified contact | [07](07-data-engine-and-waterfall.md) | `waterfall.py` | **Measured: 29.7-72.4%, median 49.8%** — and the range revised the roadmap, see below |
| Signals combine probabilistically, not additively | [06](06-signal-library.md) | `scoring.py` | **Proven.** Three 0.5 signals give 0.875, never 1.5 |
| One fresh signal beats many stale ones | [06](06-signal-library.md) | `scoring.py` | **Proven** |
| Scoring is explainable per factor | [08](08-ai-agent-layer.md), [11](11-compliance-and-governance.md) | `scoring.py` | **Proven.** Contributions sum to the score |
| Default-deny on unknown jurisdictions | [11](11-compliance-and-governance.md) | `policy.py` | **Proven.** An unmapped country requires consent |
| Opt-out outranks legal basis *and* customer overrides | [11](11-compliance-and-governance.md) | `policy.py` | **Proven** |
| Legitimate interest never substitutes for consent | [11](11-compliance-and-governance.md) | `policy.py` | **Proven** (the converse does hold) |
| Every decision carries a rule key and rationale | [11](11-compliance-and-governance.md) | `policy.py` | **Proven** |
| Overlays may tighten policy, never loosen it | [05](05-blueprints-and-adaptability.md) | `overlay.py` | **Proven.** Loosening raises `PolicyLoosened` |
| Tenants extend freely outside policy | [05](05-blueprints-and-adaptability.md) | `overlay.py` | **Proven.** Non-policy keys merge without restriction |
| `on:` cannot silently reappear in the DSL | [04](04-gtm-program-dsl.md) | `dsl.py` | **Proven.** Boolean trigger keys fail with a named error |
| Every shipped program declares a real holdout | [04](04-gtm-program-dsl.md) | `dsl.py` | **Proven** |
| The DSL has no Turing-complete expressions | [04](04-gtm-program-dsl.md) | `expr.py` | **Proven.** AST allowlist; ten escape attempts rejected |
| A program breaking compliance cannot be merged | [04](04-gtm-program-dsl.md) | `programtest.py` | **Proven.** Four deliberate breakages each fail the suite |
| A company profile resolves to an archetype deterministically | [05](05-blueprints-and-adaptability.md) | `blueprint.py` | **Proven** for all 11 archetypes — after the profile needed a 13th dimension |
| A B2C profile can never land on a B2B archetype | [05](05-blueprints-and-adaptability.md) | `blueprint.py` | **Proven.** `customer_type` and `compliance_tier` disqualify rather than deduct |

## What building it changed

**1. Three of the four example programs carried real defects.** The linter caught them, not review:

- `02-plg-product-signals` and `04-local-services-multisite` had **no suppressing exit rule**. A contact who unsubscribed would not have been recorded as suppressed and would have stayed reachable — the precise failure the invariant exists to prevent, shipped in the reference examples.
- `03-ecommerce-dtc` **auto-sent tier 2 with no eval gate**. At 100,000 contacts per week that was the highest-blast-radius play in the file, not the lowest.

**2. One product invariant was over-generalised.** "Tier 1 never auto-sends" is a B2B 1:1 rule. In high-volume B2C, tier 1 means high LTV, not human-touched, and hand-reviewing every VIP winback is not economically real. The rule is now scoped by blueprint, defaulting to human review when the blueprint is unknown — the safe failure mode, since an unnecessary review costs less than a bad send. This is the adaptability thesis meeting its own edge case: a global rule that is wrong for one archetype is exactly what blueprints exist to resolve.

**3. One overlay bug would have voided the compliance guarantee.** `channels_require_basis` is a mapping, so the generic dictionary merge recursed into it and a weakened legal basis passed through unflagged. A tenant could have downgraded `email` from `consent` to `legitimate_interest` and the resolver would have accepted it silently. Policy keys are now checked before the generic merge.

**4. A roadmap exit criterion was unmeasurable as written.** "≥30% cost reduction" did not say mean, median, or every cohort. Measured, one cohort lands at 29.7% while another reaches 72.4% — so the criterion would have been arguable in exactly the moment it mattered. It is now median ≥30% with a 20% floor on the worst cohort.

## Second round: the expression layer and the test runner

Implementing the restricted expression evaluator and the declarative test runner surfaced four more defects, three of them in artefacts already reviewed and shipped.

**5. `outcome.type = 'unsubscribe'` shipped in four of four example programs.** A single `=` where a comparison was meant. It does not parse as an expression at all, so the exit rule could never have fired — meaning opt-outs would never have been recorded. This is the third time the same failure class has appeared in this repository, each time in a different disguise, each time found by execution rather than by reading. That pattern is now itself a finding.

**6. `evaluate` crashed on absent fields while its own docstring promised the opposite.** An unresolved path became `None`, and `None >= 0.9` raises `TypeError` rather than evaluating false — a running program would have died mid-flight on any missing payload key. Absent fields now resolve to a sentinel whose comparisons all return False, including `!=`: an expression cannot conclude anything about a value it does not have.

**7. Enrollment was gated on signal decay instead of the declared trigger window.** They answer different questions — the window decides whether an event is still in scope, decay weights how much it counts — and conflating them let a 90-day-old signal enrol under a 30-day window.

**8. A tier can key off any scored field, not only `score`.** The ecommerce program routes tier 1 on LTV percentile. The runner passed only `score`, so it would have silently misrouted every B2C program.

## The finding that changes the pitch

The optimiser's edge is **inversely proportional to how good cheap data coverage already is in a market**. Iberia, LATAM and Poland exceed 60%. DACH and the Nordics compress toward 30%, because there the premium provider is genuinely required.

Commercially: the "we cut your data cost" argument is weakest precisely in DACH, a high-ACV target market. There, Zolts leads with governance, jurisdictional policy and incrementality. A single blended savings number would have hidden this entirely.

## Third round: the blueprint resolver

**9. Twelve profile dimensions could not resolve eleven archetypes.** A regulated fintech and a regulated life-sciences company produced byte-identical profiles across all twelve — same motion, ACV band, cycle length, compliance tier, CRM, team size. Their canonical profiles tied at exactly equal scores, and healthtech's resolved to the generic enterprise archetype instead of its own.

The tempting fix was to nudge weights until the test passed. That would have hidden the defect rather than fixed it: the model genuinely lacked the information needed to tell the two apart. `sector` was added as the thirteenth dimension, and `docs/05` corrected.

This is the first finding that changed the *specification* rather than an implementation of it. It is also the clearest case so far of a document reading as complete because nothing had tried to execute it.

## The pattern worth naming

Nine defects so far. Every one was found by executing the artefacts, none by reading them — and three are the same failure in different clothing: **an opt-out path that silently does not work**. A missing `suppress` flag, then a second one, then an exit clause that cannot parse. Each looked correct in review.

The plan already classifies the policy decision log and idempotency as unacceptable debt. This adds a third: **a suppression path with no test is unacceptable debt**, because it fails silently, it fails in the direction of contacting people who asked not to be contacted, and human review demonstrably does not catch it.

## Deliberately not implemented

The durable runtime (Temporal), connectors, the agent layer and its eval harness, deliverability scheduling, and persistence. Those need infrastructure, not logic, and simulating them would prove nothing. Everything here is pure logic that either holds or does not.
