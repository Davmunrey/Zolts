# 09 — Multichannel execution and deliverability

## Thesis

> Reputable sending capacity is the scarce resource of modern GTM. With AI agents multiplying outbound volume, the constraint stops being "how many leads do I have" and becomes "how many messages can I send without burning my domain". Zolts models reputation as **managed inventory**, the way an ads system manages budget.

## Sending capacity model

```
daily_capacity = Σ_domains Σ_mailboxes ( base_cap × warmup_factor × reputation_factor )

warmup_factor    ∈ [0.1, 1.0] following the warm-up curve (weeks 1-6)
reputation_factor ∈ [0, 1.2]  a function of bounces, complaints, engagement, Postmaster data
```

The scheduler assigns each send to a specific mailbox, optimising for mailbox reputation, geographic and language affinity with the recipient, the recipient's MX provider (segregating Google-bound from Microsoft-bound traffic), and load balance. A mailbox whose reputation degrades is automatically pulled back into warm-up.

## Operating thresholds (non-negotiable)

| Metric | Target | Alarm | Automatic cut-off |
|---|---|---|---|
| Bounce rate | <1.5% | 2% | 3% → program paused |
| Spam complaints | <0.05% | 0.1% | 0.3% → domain paused (major providers' hard threshold) |
| Reply rate | >4% | <2% | <1% → mandatory segment and copy review |
| Unsubscribe | <0.5% | 1% | 2% → review |
| Emails per mailbox per day | 30-40 | 50 | 60 |
| Simultaneous new domains | — | — | Staggered ramp, never a mass activation |

## Domain technical requirements (automated checklist)

- SPF, DKIM (2048-bit), **DMARC at minimum `p=quarantine`**, with aggregate reports monitored.
- `List-Unsubscribe` plus `List-Unsubscribe-Post` (one-click): required for bulk senders since 2024; its absence is grounds for direct filtering.
- Sending domains separated from the primary corporate domain (`get.brand.com`, `brand-hq.com`), never the billing domain.
- Plain text first, no tracking pixels on the first touch, no public URL shorteners, links on an owned domain.
- Blocklist and Google Postmaster Tools monitoring via API, alerting before delivery rates fall.
- Verification immediately before each send (a 90-day-old datum is no longer valid).

## Channels and their own rules

| Channel | Practical limit | Specific risk | Control in Zolts |
|---|---|---|---|
| Email | See above | Domain reputation | Capacity scheduler plus circuit breakers |
| LinkedIn | ~20-25 invitations per day per account | Account restriction or ban | Hard quotas, human-like jitter, no unsupported browser automation |
| Voice | Legal hours per country | Suppression lists (national do-not-call registries) | Mandatory check before the task is created |
| WhatsApp | Approved templates | Consent required | Only with verifiable `consent` basis |
| Ads | Budget | Minimum audience sizes, PII matching | Local hashing, minimum sizes, customer exclusion |
| Product (in-app) | — | User fatigue | Global frequency shared with email |
| Human task | Team capacity | An infinite queue means zero execution | Per-tier capacity and SLA with task expiry |

## Cross-channel coordination

A contact is a person, not a per-channel record. The **frequency cap is global**: if they received an email and a LinkedIn invitation this week, the third touch is delayed even if it comes from a different program. A unified suppression engine covers opt-outs, current customers, open opportunities, competitor accounts, legal suppression lists, and owner-level do-not-contact flags.

## Reputation → decision loop

Delivery events feed the scoring model: an account whose domain bounces systematically loses `reachability`, which moves it from the email tier to the ads or call tier. **Deliverability is not an isolated infrastructure problem: it is an input to the decision engine.** That integration is hard to replicate for anyone whose execution lives in a separate tool.
