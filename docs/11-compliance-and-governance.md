# 11 — Compliance and governance

> **Note:** this document describes product and engineering requirements. It is not legal advice. Every policy pack must be validated by the customer's counsel before it is activated in production.

## Strategic thesis

Regulation is not a cost: it is the moat. The harsher the environment (GDPR, ePrivacy, the AI Act, bulk-sender rules), the more "spray and pray" tooling is pushed out of the European enterprise market. Zolts turns compliance into **a product function competitors cannot bolt on later**, because it requires policy to be evaluated *before* every action, inside the runtime core.

## Policy engine

Every external action passes a blocking evaluation:

```
evaluate(subject, action, context) → { allow | deny | review, rule_key, rationale }
```

Inputs: the contact's effective jurisdiction (country of residence, not of the domain), the legal basis declared per channel, consent state and its provenance, suppression and exclusion lists, frequency limits, local quiet hours, tenant quotas, remaining budget, and the classification of AI-generated content.

Every decision is persisted in `policy_decision` (append-only, 24 months). A DPO can answer "why did this person receive this message?" with a query, not an investigation.

## Jurisdiction matrix (policy pack v1 extract)

| Jurisdiction | B2B email | B2B calling | Implementation notes |
|---|---|---|---|
| Spain | Legitimate interest with clear opt-out | Subject to the Robinson list | LSSI plus LOPDGDD; mandatory check against the advertising exclusion list |
| Germany | Very restrictive in practice; consent as the prudent standard | Restricted | The default pack blocks cold B2B email in DE unless the customer documents an override |
| France | B2B legitimate interest with opt-out and relevance to the professional role | Subject to Bloctel | The role must be relevant to the offer: validated in the segment |
| United Kingdom | Corporate subscribers exempt from consent (PECR); individuals and sole traders are not | Subject to TPS/CTPS | The account's legal form changes the rule, so it is modelled as a field |
| Netherlands / Nordics | Legitimate interest with opt-out | Varies | — |
| United States | CAN-SPAM: opt-out, physical address, no deceptive headers | TCPA for mobile and SMS | High risk on mobile: prior consent required |
| Canada | CASL: consent (express, or implied within a window) | — | Default pack: blocked unless recorded consent exists |
| LATAM | Varies by country (LGPD in Brazil, LFPDPPP in Mexico) | Varies | Per-country packs, opt-out by default |

The pack is **data, not code**: it updates without a deployment and is versioned. A regulatory change propagates to every tenant with a changelog and a DPO notification.

## GDPR: concrete implementation

| Obligation | Product implementation |
|---|---|
| Legal basis | Declared per channel and per program; without one, the runtime does not execute |
| Legitimate interest assessment | Guided template, stored and versioned per tenant and program |
| Minimisation | Enrichment requests only the fields the program declares it needs |
| Accuracy | Every value retains source, date and confidence; corrections propagate in cascade |
| Transparency | Privacy notice and data provenance can be injected automatically into the first contact |
| Data subject rights | Identity-graph lookup → cascade export or erasure, propagation to sub-processors, certificate |
| Retention | Per-class, per-jurisdiction TTL executed by an audited job |
| Records of processing | Generated automatically from live configuration, not maintained by hand |
| Sub-processors | Register with DPA, region and status; alert when a new provider is added |
| International transfers | Regional residency plus control over which providers may process which region's data |
| Security | Encryption in transit and at rest, PII in a vault with per-tenant keys, purpose-logged access |

## EU AI Act: applicable obligations

Typical GTM usage falls in the **limited risk** band (transparency obligations), but the design assumes the stricter scenario:

- **AI content disclosure** configurable per jurisdiction and channel, with default templates.
- **System traceability**: model, prompt version, sources, evals and approver for every piece of content.
- **Meaningful human oversight**: eval gating and human-reviewed tiers are the mechanism, not a checkbox.
- **Technical documentation of the scoring system**: per-factor explainability is mandatory in the engine (`explain: true` is not optional).
- **Prohibitions**: no inference of special categories of data (health, orientation, religion, union membership, political belief) and no scoring based on them. A hard engine rule, not a setting.

## Security and certifications (sequence)

| Phase | Milestone | Why in that order |
|---|---|---|
| Day 1 | Encryption, RLS, MFA, access logging, secret management | Marginal cost is zero if done from the start |
| Month 3 | Full GDPR pack (DPA, RoPA, sub-processors, DSAR) | Unlocks the European mid-market |
| Month 6 | SOC 2 Type I plus external pentest | Entry price for procurement |
| Month 12 | SOC 2 Type II | Unlocks enterprise |
| Month 15+ | ISO 27001 (if pipeline demands it) | Only on real pipeline demand, not before |

## Customer-side governance

Roles (RBAC): `owner`, `gtm_engineer` (edits programs), `operator` (executes and reviews), `analyst` (read-only), `dpo` (audit and policy veto, no execution access). The DPO can block a program; nobody can unblock it without their recorded sign-off. Mandatory approvals are configurable per compliance tier.
