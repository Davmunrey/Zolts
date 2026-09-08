# 03 — Canonical data model

## Core entities

| Entity | Description | Resolution key |
|---|---|---|
| `account` | Organisation (company, account, local site) | root domain + legal registration + fuzzy name/geo |
| `person` | Individual in a professional context | verified email > LinkedIn URN > (name + domain) |
| `membership` | Person↔account relationship with role and validity | historical: supports job-change signals |
| `signal` | Observed event with entity, type, strength, source, timestamp | immutable, append-only |
| `segment` | Computed population (SQL or DSL) with a version | materialised with TTL |
| `program` | Versioned definition of a GTM play | semver + content hash |
| `enrollment` | An account or person instantiated into a program | idempotent per (program_version, entity) |
| `touch` | Action executed toward an entity through a channel | unique idempotency_key |
| `outcome` | Attributable result (reply, meeting, opp, won, churn) | linked to enrollment and experiment variant |
| `experiment` | Holdout/variant with deterministic assignment | stable hash |
| `policy_decision` | Record of a policy evaluation (allow/deny plus reason) | append-only, 24-month retention |
| `cost_event` | Attributed cost (credit, token, send, provider) | basis of the per-play P&L |
| `tenant_baseline` | What the tenant's GTM cost and produced in the ninety days before Zolts: four spend fields, the funnel, the derived cost per meeting and per opportunity, and a digest the pilot letter quotes. Written once (ADR-042) | one per tenant |
| `incrementality_report` | What one program did against its holdout as of the end of one billing period: arms, lift beside the minimum detectable effect, the unread share, decisions, credits by kind, pipeline on opportunities only, the baseline's digest, a verdict in three words. The canonical fields, a digest over them and the rendered document, stored verbatim. Written once (ADR-043) | one per program per closed period |

## Identity graph

Resolution at three levels, with confidence scoring and survivorship rules:

1. **Deterministic:** normalised email, corporate domain, LinkedIn URN, tax ID (VAT/EIN), CRM ID.
2. **Probabilistic:** name, geo, sector and size similarity; threshold configurable per tenant (stricter means fewer false positives and higher enrichment cost).
3. **Human:** review queue for collisions above the impact threshold (for example accounts with an open opportunity).

Per-field survivorship: `precedence = [customer CRM, provider with highest measured accuracy, most recent observation]`. Every value retains `source`, `observed_at` and `confidence`, so any datum is explainable and revocable — the GDPR accuracy requirement.

**Never merge destructively.** Merges are reversible: a derived `golden_record` is materialised while source records persist.

## Multi-tenancy and extensibility

- Isolation by `tenant_id` plus row-level security on every table.
- Custom fields in `attributes JSONB`, validated against a **tenant-declared schema** (`tenant_schema`), versioned. This is what lets one core serve an industrial manufacturer and a PLG SaaS company.
- All configuration objects (segments, programs, signals, policies) inherit by overlay: `blueprint → industry_pack → tenant → program`. See [05](05-blueprints-and-adaptability.md).

## Retention and minimisation

| Data class | Default retention | Note |
|---|---|---|
| PII of non-converted contacts | 12 months from last contact | Configurable per jurisdiction |
| Raw signals | 13 months | Non-PII aggregates retained indefinitely |
| Execution traces | 24 months | Audit requirement |
| AI-generated content | 24 months | AI Act traceability |
| Embedding vectors over PII | Tied to source TTL | Cascade deletion |

Automated DSAR (access and erasure): a request resolves through the identity graph and executes cascade deletion plus propagation to sub-processors, with an execution certificate.

## Reference DDL

See [`examples/sql/schema.sql`](../examples/sql/schema.sql).
