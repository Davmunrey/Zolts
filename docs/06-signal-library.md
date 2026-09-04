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

## Signal hygiene

- **Noise suppression:** a signal whose measured lift against holdout does not beat baseline within 90 days is automatically downgraded to `advisory` (it stops triggering programs) and the owner is notified. The catalogue prunes itself.
- **Signal cost in the P&L:** every signal reports monthly cost and pipeline contribution. Paid signals that do not cover their cost are switched off.
- **Anti double-touch:** cross-program deduplication at person level; an account cannot receive three simultaneous programs even if it satisfies all three triggers (priority by score and by owner).
