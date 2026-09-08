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

Every decision is persisted in `policy_decision` (append-only, 24 months) with the pack version and digest that decided it. A DPO can answer "why did this person receive this message?" with a query, not an investigation — and the query returns the rule text that applied at the time.

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

The pack is **data, not code**: a row, published by an operator with `zolts policy-pack --file … --version 2`, one active at a time, and no deployment involved (ADR-044). It was this sentence beside a dict in `zolts/policy.py` until D-53. What still needs building is the notification half: a regulatory change reaches every tenant immediately because there is one active pack, and telling their DPO it happened is an operator writing an email.

**A decision names the rules that made it.** Every `policy_decision` carries the version and the `sha256` digest of the pack that produced it, and the document behind that digest is kept — so *why did this person receive this message* is answered with the rules as they stood that day, not as they stand now. Decisions written before the packs were versioned carry no digest and are reported as unattributed rather than back-filled.

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

## What a model provider sees

The agent layer is **off by default** (`ZOLTS_AGENTS` unset): until a deployment turns it on, nothing leaves the runtime for a model provider. When it is on, three agents call the model, and each sends a retrieved subset rather than the record (`runtime/engine/generate.py`, `runtime/research.py`):

| Agent | What is sent | What is never sent |
|---|---|---|
| Copywriter | the account's name, employee band and country; the contact's full name; the signal payloads that enrolled them, as the source delivered them | email addresses, phone numbers, the CRM record, anything of another tenant's |
| Researcher | the account's name, domain, employee band and country; its contacts' names, titles, seniority and buying roles; its recent signals | the same |
| Triage | the text of one reply, truncated to 2,000 characters | the sender's address, the thread, the recipient |

Every call is priced before it is made and recorded after it (`docs/08`), and every generated message is a proposal that a gate or a person approves before anything is sent (ADR-012). The provider is Anthropic, through the official SDK, under the API terms in force when the deployment is configured; those terms govern retention and training, not this page, and a customer's DPA names the provider as a sub-processor.

**The endpoint is a control, and it is per deployment.** `ZOLTS_MODEL_BASE_URL` moves every call to a gateway that serves the Messages API shape — a customer's own, or one that logs, filters or pins a region — with no change to the code (ADR-011). It is one variable per deployment, not per tenant: a tenant who needs their own gateway needs their own deployment today, and per-tenant routing is decision 36. ADR-011 read as though a tenant could route on their own, and `docs/23` repeated it as a mitigation; both said more than the code does (D-42).

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
