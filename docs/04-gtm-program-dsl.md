# 04 — GTM Program DSL (config-as-code)

## Why a DSL and not a UI

| Without a DSL (today's state of the art) | With a DSL |
|---|---|
| Nobody knows who changed the filter that broke the campaign | `git blame` |
| No staging environment | `zolts plan` plus dry-run over a snapshot |
| Rollback means redoing it by hand | `zolts rollback --to 1.4.2` |
| Impossible to audit for a DPO or a CFO | Signed diff plus a per-execution trace |
| Every customer becomes a fork | Overlays on a shared blueprint |

The UI (Studio) is a bidirectional editor over this YAML. A GTM engineer works in the repository; an AE works in Studio; both produce the same artefact.

## Anatomy of a Program

```yaml
apiVersion: zolts/v1
kind: Program
metadata:
  key: series-a-hiring-surge
  version: 2.1.0
  owner: gtm-eng@company.com
  blueprint: b2b-saas-sales-led      # inheritance
spec:
  trigger:                            # 1. SIGNAL
  audience:                           # 2. SEGMENT
  enrich:                             # 3. ENRICHMENT
  score:                              # 4. DECISION
  route:                              # 5. TIER ASSIGNMENT
  plays:                              # 6. PLAYS PER TIER
  policy:                             # 7. GOVERNANCE
  experiment:                         # 8. MEASUREMENT (mandatory)
  budget:                             # 9. ECONOMIC LIMITS
  exit:                               # 10. EXIT CONDITIONS
```

### Blocks

| Block | Contract | Engineering note |
|---|---|---|
| `trigger` | `events: [signal.type]` plus `window` and `dedupe` | Event-driven by default; `schedule` only for reconciliation |
| `audience` | SQL over the semantic layer, or a `Segment` reference | Compiled and explained (`EXPLAIN`) before publishing |
| `enrich` | Required fields plus accuracy SLA and a cost ceiling | The *router* picks providers, not the user |
| `score` | Declarative PIT-R formula or a trained model (`model_ref`) | Must return per-factor contribution (explainability) |
| `route` | Thresholds to tier (t1/t2/t3) plus per-tier capacity | Human capacity is a finite resource and is modelled as one |
| `plays` | Channel step sequence with waits and branching | Every step emits a `proposed_action`; it never executes directly |
| `policy` | Overrides on tenant policy (stricter only) | A program **cannot** relax global policy |
| `experiment` | Mandatory `holdout_pct` ≥ 5% (a waiver requires justification) | Without it, `zolts apply` refuses |
| `budget` | Credits per month, max cost per account, max cost per meeting | The tenant's credit ceiling stops execution; the per-programme fields do not yet, and `zolts validate` names the ones that are decoration (D-63). `max_cost_per_meeting` is **reported and never enforced** — it appears on the frozen report beside the cost the period achieved, because a programme is above it every day until the first meeting lands (decision 45) |
| `exit` | Reply, meeting, opportunity created, unsubscribe, exhaustion | Prevents the classic "we kept emailing an existing customer" |

## Lifecycle and testing

```
zolts lint            → schema validation plus static policy rules
zolts plan            → dry-run over a snapshot: account count, cost estimate, sample messages
zolts test            → declarative tests (account fixtures → expected decisions)
zolts apply --stage   → shadow mode: everything computed, nothing sent
zolts apply --live    → publishes the version and starts enrollments
zolts rollback        → reverts to the prior version; active enrollments drain, they are not cut
```

**Declarative tests** are the differentiator against Clay and n8n:

```yaml
kind: ProgramTest
program: series-a-hiring-surge@2.1.0
cases:
  - name: EU contact without a legal basis receives no email
    given:
      account: {country: DE, employee_band: "51-200"}
      person:  {country: DE, consent_state: {}}
    expect:
      policy_decision: deny
      rule: eprivacy.de.b2b_email_requires_consent
  - name: an expired signal does not trigger
    given:
      signal: {type: funding.round, observed_at: "-45d", half_life_h: 720}
    expect:
      enrolled: false
  - name: tier 1 routes to a human, not an agent
    given: {score: 88}
    expect: {tier: t1, play: exec-1to1, auto_send: false}
```

These run in CI via `scripts/run_program_tests.py`. A program change that breaks a compliance rule **cannot be merged** — and that is verified, not asserted: `tests/test_programtest.py` deliberately relaxes a tier threshold, enables auto-send on tier 1, widens the trigger window and drops a qualifying clause, and requires the suite to fail on each. A guarantee nothing tries to break is not a guarantee.

Shipped suites live in `examples/tests/`. Each is required to contain at least one denial case: a suite that only asserts happy paths proves nothing about compliance, and that requirement is itself a test.

This is the artefact an enterprise DPO is shown.

## DSL resources

`Program`, `Segment`, `SignalDefinition`, `PlayTemplate`, `MessageTemplate`, `Policy`, `Blueprint`, `Connector`, `ScoreModel`, `ProgramTest`.

Formal schema: [`examples/schema/zolts-program.schema.json`](../examples/schema/zolts-program.schema.json).
Complete examples: [`examples/programs/`](../examples/programs/).

## DSL design notes

- **`events:` and not `on:`** — YAML 1.1 coerces `on`/`off`/`yes`/`no` to booleans. Using `on:` as a key yields `True` in PyYAML parsers and breaks validation silently (the same bug GitHub Actions carries). All four example programs validate against the JSON Schema via `scripts/validate.py`.
- **No Turing-complete expressions.** `where` and `when` are restricted boolean expressions (a CEL-like subset). An arbitrary executable DSL blocks static policy analysis and turns the engine into an unsafe interpreter.
- **Expressions are a restricted subset**, enforced by AST allowlist in `zolts/expr.py`, not by pattern matching on source text (a substring blocklist is trivially bypassed). Dotted field paths are permitted, bounded to depth 3, rooted in a plain name, with underscore-prefixed attributes refused — which closes `x.__class__.__bases__` and every variant of it. Arithmetic is allowed because computed thresholds need it; exponentiation is not, because `2 ** 999999999` is an outage rather than a feature.
- **An absent field makes a clause false, never an error.** A missing payload key resolves to a sentinel whose comparisons all return False, including `!=`. An expression cannot conclude anything about a value it does not have, and the safe conclusion is not to act.
- **The trigger window governs enrollment; decay governs scoring.** They are different questions and conflating them lets an expired signal enrol.
- **Semver carries meaning:** `major` changes the population or the legal basis (requires owner re-consent); `minor` adds steps or channels; `patch` covers copy and thresholds.
- **Active enrollments are never cut on rollback**: they drain on the version they entered with. Cutting sequences mid-flight damages the prospect experience and corrupts the experiments.
