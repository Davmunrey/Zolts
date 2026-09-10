# 03 — Canonical data model

## Core entities

| Entity | Description | Resolution key |
|---|---|---|
| `account` | Organisation (company, account, local site) | root domain + legal registration + fuzzy name/geo |
| `person` | Individual in a professional context | verified email > LinkedIn URN > (name + domain) |
| `membership` | Person↔account relationship with role and validity | historical: supports job-change signals |
| `signal` | Observed event with entity, type, strength, source, timestamp | immutable, append-only |
| `segment` | Computed population (SQL or DSL) with a version | **Not a table.** It is the audience block inside a programme spec, versioned with the programme (ADR-034) and evaluated per enrolment rather than materialised |
| `program` | Versioned definition of a GTM play | semver + content hash |
| `enrollment` | An account or person instantiated into a program | idempotent per (program_version, entity) |
| `touch` | Action executed toward an entity through a channel | unique idempotency_key |
| `outcome` | Attributable result (reply, meeting, opp, won, churn) | linked to enrollment and experiment variant |
| `experiment` | Holdout/variant with deterministic assignment | **Not a table.** It is a deterministic hash over the enrolment, stored as the variant on the enrolment row — no state to keep, so nothing to disagree with |
| `policy_decision` | Record of a policy evaluation (allow/deny plus reason) | append-only, 24-month retention |
| `cost_event` | Attributed cost (credit, token, send, provider) | basis of the per-play P&L |
| `tenant_baseline` | What the tenant's GTM cost and produced in the ninety days before Zolts: four spend fields, the funnel, the derived cost per meeting and per opportunity, and a digest the pilot letter quotes. Written once (ADR-042) | one per tenant |
| `incrementality_report` | What one program did against its holdout as of the end of one billing period: arms, lift beside the minimum detectable effect, the unread share, decisions, credits by kind, pipeline on opportunities only, the baseline's digest, a verdict in three words. The canonical fields, a digest over them and the rendered document, stored verbatim. Written once (ADR-043) | one per program per closed period |

**Two of the fourteen are not rows, and the table above says which.** A documented entity with no table is this repository's dominant defect shape, so the two that are versioned configuration name where they really live rather than sending a reader to look for a table (D-88). `tests/test_data_model.py` fails in both directions: while they are configuration the document must say so, and the day either becomes a table the document is stale.

## Identity graph

Resolution at three levels, with confidence scoring and survivorship rules:

1. **Deterministic:** normalised email, corporate domain, LinkedIn URN, tax ID (VAT/EIN), CRM ID.
2. **Probabilistic:** name, geo, sector and size similarity; threshold configurable per tenant (stricter means fewer false positives and higher enrichment cost).
3. **Human:** review queue for collisions above the impact threshold (for example accounts with an open opportunity).

Per-field survivorship: `precedence = [customer CRM, provider with highest measured accuracy, most recent observation]`. Every value retains `source`, `observed_at` and `confidence`, so any datum is explainable and revocable — the GDPR accuracy requirement.

**Never merge destructively.** Merges are reversible: a derived `golden_record` is materialised while source records persist.

## Multi-tenancy and extensibility

- Isolation by `tenant_id` plus row-level security on every table.
- Custom fields in `attributes JSONB`. **The validation is not built:** `tenant_schema` is named here and appears nowhere in the runtime, so an attribute is stored as sent and nothing checks it against a declared shape (D-89). What ships instead is the blueprint overlay below, which is what actually lets one core serve an industrial manufacturer and a PLG SaaS company. COMP-3 in `docs/24`.
- All configuration objects (segments, programs, signals, policies) inherit by overlay: `blueprint → industry_pack → tenant → program`. See [05](05-blueprints-and-adaptability.md).

## Retention and minimisation

| Data class | Default retention | Note |
|---|---|---|
| PII of non-converted contacts | 12 months from last contact | Configurable per jurisdiction |
| Raw signals | 13 months | Non-PII aggregates retained indefinitely |
| Execution traces | 24 months | Audit requirement |
| AI-generated content | 24 months | AI Act traceability |
| Embedding vectors over PII | Tied to source TTL | Cascade deletion |

**None of the retention above is executed, and there is no DSAR path.** Nothing in the runtime purges a row on age: the windows in that table are the policy this product intends to enforce, not a job it runs, and no code resolves a subject request, cascades a deletion or issues a certificate (D-89). Stated here in the document that publishes the windows, because a retention table a reader takes for a running control is worse than no table — it is the answer a DPO writes down. The work is COMP-1 and COMP-2 in `docs/24`; decision 54 registers what a first retention job deletes and what it must never delete.

**Row-level security is on every tenant-scoped table, and that is measured rather than asserted.** `003_rls.sql` forces it over a hard-coded list of sixteen names and every table added since was forced by somebody remembering; `tests/test_data_model.py` asks `pg_class` instead, and requires enabled, forced and a policy on every table carrying a `tenant_id` (ADR-054).

## Reference DDL

See [`examples/sql/schema.sql`](../examples/sql/schema.sql).
