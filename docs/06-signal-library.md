# 06 — Signal library

## Operating thesis

> The message matters less than the moment. **Time-to-touch is the highest-leverage variable in the whole GTM system**: the same play executed within an hour rather than after 72 hours changes the reply rate by a factor of two to four. Zolts treats signal-to-action latency as a product SLA, not an implementation detail.

## Anatomy of a signal

```yaml
apiVersion: zolts/v1
kind: SignalDefinition
metadata: {key: hiring.role_opened}
spec:
  entity: account
  source: {connector: jobs_feed, refresh: 6h}
  freshness_sla: 12h          # maximum acceptable data age
  half_life_h: 720            # decay: worth half after 30 days
  base_strength: 0.6
  legal_basis: legitimate_interest
  cost_per_check_eur: 0.004
  dedupe_window: 14d
  payload_schema: {department: string, count: int, seniority: string, jd_keywords: [string]}
```

Every signal declares **cost**, **decay** and **legal basis**. Without all three it can be neither budgeted nor audited.

## Catalogue (v1)

### Tier A — high intent, fast decay (action SLA under 4 hours)

| Signal | Source | Half-life | Use |
|---|---|---|---|
| `web.pricing_page_visit` | Web deanonymisation (RB2B/Vector/first-party) | 48h | A pricing visitor is the hottest lead that exists |
| `web.docs_or_api_visit` | First-party analytics | 72h | Technical signal: the evaluator, not the buyer |
| `product.limit_hit` | Product event | 96h | PLS: friction means budget unlocked |
| `inbound.form_submitted` | Forms | 1h | Speed to lead: every 10 minutes of delay costs conversion |
| `review.competitor_page_view` | G2/Capterra intent | 96h | Active comparison means an open cycle |
| `event.booth_scan` / `webinar_attended` | Events | 120h | Short post-event window |

### Tier B — structural change, medium decay (action SLA under 48 hours)

| Signal | Source | Half-life | Use |
|---|---|---|---|
| `funding.round` | Crunchbase/Harmonic/press | 30d | New budget plus growth pressure |
| `people.job_change` | LinkedIn/UserGems | 45d | A champion changing company is the best lead in the world |
| `people.exec_hired` | LinkedIn/press | 60d | A new executive has 90 days to make a mark, which means buying |
| `hiring.role_opened` | Job feeds | 30d | A job posting is a public specification of a pain |
| `tech.install_detected` / `tech.uninstall` | BuiltWith/HG/DNS | 45d | Complement to, or displacement of, a competitor |
| `corp.m_and_a` | Press/registry | 60d | Stack consolidation: a replacement window |
| `local.new_location_detected` | Google Business/registries | 60d | Operational expansion |

### Tier C — context and fit, slow decay (feeds fit, not timing)

| Signal | Source | Use |
|---|---|---|
| `firmo.headcount_growth_by_dept` | LinkedIn/providers | Investment trend by function |
| `intent.topic_surge` | Bombora/6sense | Account-level category intent |
| `content.engagement` | Social/newsletter | Identifies latent champions |
| `public.tender_published` | Official gazettes (TED and national) | Public sector and industrial |
| `esg.report_published` | Public registries | Compliance-driven selling |
| `commerce.app_installed` | Shopify/marketplaces | Ecommerce stack and maturity |
| `partner.shared_customer` | Partner data | The channel with the highest close rate |

### Tier D — risk and expansion (post-sale)

| Signal | Source | Use |
|---|---|---|
| `cs.champion_left` | CRM/LinkedIn | The number one churn predictor |
| `cs.usage_decline` | Product | Early churn |
| `cs.renewal_window` | CRM | Renewal timing |
| `cs.expansion_headroom` | Product | Quantified upsell |

## Composition: compound signals win

An isolated signal has poor precision. The value is in **windowed conjunction**:

```
funding.round (30d) ∧ hiring.role_opened[revops] (30d) ∧ tech.uninstall[competitor] (45d)
→ roughly 6-9× baseline opportunity probability
```

Zolts models this as `combine: all_within` in the trigger and penalises over-conjunction (a population of three accounts is not a program, it is a task).

## Decay function

```
strength_t   = base_strength × 0.5^(Δt / half_life_h) × confidence_source
intent_score = 1 - Π(1 - strength_t,i)     # probabilistic combination, not a sum
```

Probabilistic combination avoids the classic error of summing signals and saturating the score with correlated noise.

## Time-to-touch SLA (product commitment)

| Signal tier | Ingestion → signal available | Signal → action proposed | Signal → action executed |
|---|---|---|---|
| A | p95 < 5 min | p95 < 10 min | p95 < 60 min |
| B | p95 < 60 min | p95 < 2h | p95 < 24h |
| C | p95 < 24h | daily batch | daily batch |
| D | p95 < 6h | p95 < 12h | per the CS playbook |

These are engineering KPIs, not marketing aspirations: they appear on the customer dashboard and in the contractual SLA of the Scale and Enterprise plans.

**What the runtime measures against that table.** A target nobody checks is a
number in a document, and this one is written into two plans. The verdict is
computed per signal tier and never per programme (decision 57): a programme may
consume signals of several tiers, and a Tier C signal is a daily batch by
design, so judging the whole programme by its tightest tier would report a
failure on a system doing exactly what its catalogue says.

| Stage | Status | Measured as |
|---|---|---|
| Ingestion → signal available | **Built** | `signal.ingested_at` less `signal.received_at`, p95 per tier. The arrival is stamped before the transaction opens, so the stage measures the trigger evaluation across every live programme — the part that grows with the programme count (SIG-1). Not `observed_at`: that is how long the *source* took to notice, upstream of every column here |
| Signal → action proposed | **Built** | `proposal.created_at` less `signal.ingested_at`, p95 per tier |
| Signal → action executed | **Built** | `touch.sent_at` less `signal.ingested_at`, p95 per tier. Sent, not queued |

The targets live in `zolts/latency.py` and a test reads this table to check
them, the way `docs/12`'s price list is read (ADR-017). It fails in both
directions: a stage marked **Not measured** that acquires a probe fails the
suite until this page is corrected — which is how the first row moved, rather
than somebody remembering to move it — so the page cannot quietly fall behind
the runtime. A signal written before migration 029 carries no arrival time and
is excluded rather than counted as instantaneous, which is the direction that
would flatter the number.

## Signal hygiene

- **What each signal produced — Built, as a funnel and not as an attribution.** Per catalogue signal: fired, listened to by how many live programmes, enrolled, held out, reached, and converted inside the metric window the enrolling programme declares. Every catalogue signal has a row, the ones that never fired included; `runtime.signalfunnel`, `GET /v1/signals/funnel`, the Signals screen (ADR-066). It answers *what did this signal produce*, not *what did it cause*: the holdout converts too and is counted, so the two hygiene rules below stay unbuilt on the instrument they need.
- **Noise suppression — Not built (SIG-2).** The design: a signal whose measured lift against holdout does not beat baseline within 90 days is downgraded to `advisory`, stops triggering programs, and the owner is notified, so the catalogue prunes itself. Nothing does this. There is no `advisory` state on a signal anywhere in the runtime, and the instrument it needs — lift per *signal* rather than per programme — does not exist either: `runtime/reporting.py` measures a programme's arms, and a programme consuming three signals cannot attribute its lift among them without a design for that attribution.
- **Signal cost in the P&L — Not built (SIG-3).** The design: every signal reports monthly cost and pipeline contribution, and paid signals that do not cover their cost are switched off. Nothing computes either half. The cost half is the closer one — `signal.check` is priced and billed per account-day (`docs/12`) so the spend per signal is already in `cost_event` and only needs grouping — and the contribution half needs the same per-signal attribution SIG-2 needs.
- **Anti double-touch:** cross-program deduplication at person level — enforced by the frequency cap, which counts touches per person across every programme and channel (ADR-056). It counted an account's enrolment in one programme until D-92. **Priority by score and by owner is not built:** nothing ranks which of three eligible programmes wins, and there is no owner in the data model to rank by (D-82, decision 50). What happens today is that the cap holds the later touches; which programme got there first is whichever the planner reached first.
