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

## GDPR: what ships, and what does not

This table was written in the present tense for eleven obligations, and **6 of them had no
implementation at all** (D-89). It is the page a data protection
officer reads in diligence, so every row now carries what the runtime actually
does today and `tests/test_compliance_claims.py` checks each one against the
code on every push — in both directions. A row marked *Not built* whose
mechanism appears in the runtime fails this suite, so the day one ships the
document is corrected rather than quietly overtaken.

| Obligation | Status | What the runtime does today |
|---|---|---|
| Legal basis | **Built** | The published pack declares a required basis per channel and jurisdiction; `zolts.policy` denies without one and the decision records which pack decided (ADR-044) |
| Minimisation | **Built** | Enrichment buys only the fields a programme declares, and a field the price list does not carry is refused at publish (ADR-035) |
| Transparency · provenance | **Built** | Every claim in a generated message maps to a retrieved span, a CRM field or the proof library, and one that does not is struck out and shown (ADR-012) |
| Accuracy | **Partly built** | An enriched value keeps its provider, its timestamp and the measured hit rate behind it (`enrichment_attempt`, ADR-021). Corrections do not propagate in cascade |
| International transfers | **Partly built** | `tenant.region` is stored and the model endpoint is pinnable per deployment (ADR-011). There is no per-region control over which data provider may process which tenant |
| Security | **Partly built** | TLS in transit, connector secrets sealed at rest with a rotatable key (SEC-1), and an audit log of the actions a person took (ADR-026). There is no per-tenant key, no PII vault and no purpose log; MFA needs an identity model that does not exist (decision 50) |
| Transparency · privacy notice | **Not built** | Nothing injects a privacy notice into a first contact. COMP-4 |
| Legitimate interest assessment | **Not built** | `legitimate_interest` is a basis a programme may declare. No assessment is captured, stored or versioned. COMP-5 |
| Data subject rights | **Not built** | No code resolves a subject request, exports or erases through the identity graph, propagates to a sub-processor or issues a certificate. COMP-2 |
| Retention | **Not built** | Nothing purges a row on age. The windows in `docs/03` are the intended policy, not a running job. COMP-1, decision 54 |
| Records of processing | **Not built** | No RoPA is generated from configuration. COMP-6 |
| Sub-processors | **Not built** | No register exists, so nothing alerts when a provider is added. COMP-3 |

**Why the split is worth publishing rather than tidying away.** A buyer's DPO
asks to see the mechanism behind each row, and a row that cannot be shown costs
more than a row that says *not yet*: the first is discovered in the room, the
second is a roadmap. Five of these need a founder or counsel before they need an
engineer — which jurisdictions require what, who the sub-processors are, what a
first retention job may delete — and `docs/24` sizes each one.

## EU AI Act: applicable obligations

Typical GTM usage falls in the **limited risk** band (transparency obligations), but the design assumes the stricter scenario:

- **System traceability** — *built*: every proposal records the model, the evidence it was written from, the eval scores and who or what approved it, and nothing is sent that is not a proposal first (ADR-012).
- **Meaningful human oversight** — *built*: eval gating and human-reviewed tiers are the mechanism, not a checkbox, and the review queue is a screen an operator works.
- **Technical documentation of the scoring system** — *built*: per-factor explainability is mandatory in `zolts/scoring.py`; `explain: true` is not optional.
- **AI content disclosure** — *not switched on*: `evals.compliance_checks` verifies a disclosure marker is present and refuses the draft without one, and **nothing ever asks it to**. `requires_ai_disclosure` defaults to `False` at every call site and no jurisdiction, channel or pack sets it, so the check has never run in anger (D-90). The pack is where the answer belongs — a regulatory change ships without a deployment (ADR-044) — and which jurisdictions require the marker is decision 55. There are no default templates.
- **Prohibitions** — *not enforced*: `spec.policy.special_category_inference` accepts one value, `forbidden`, and `zolts/controls.py` registers it as a control nothing reads. No enrichment field the price list carries is a special category, so a check written today would pass on every programme without proving anything; it becomes real when a field that could carry one is priced. It is a setting, not the hard engine rule this line claimed.

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
| Day 1 | Encryption, RLS, access logging, secret management | Marginal cost is zero if done from the start. **MFA is not on this list any more:** it needs an identity model this product does not have (decision 50), and a security sequence that claims it on day one is a sequence nobody can audit |
| Month 3 | Full GDPR pack (DPA, RoPA, sub-processors, DSAR) | Unlocks the European mid-market |
| Month 6 | SOC 2 Type I plus external pentest | Entry price for procurement |
| Month 12 | SOC 2 Type II | Unlocks enterprise |
| Month 15+ | ISO 27001 (if pipeline demands it) | Only on real pipeline demand, not before |

## Customer-side governance

**None of this exists yet, and the reason is one decision up the stack.** There is no identity model in this product: a caller is a tenant and an API key, the review queue records `approved_by` as `key:<uuid>` rather than a person, and no `user`, `seat` or `role` table appears in any migration (D-82). So the five roles below are a design, not a permission system, and a DPO reading this page would have found nothing to configure (D-89).

Designed: `owner`, `gtm_engineer` (edits programs), `operator` (executes and reviews), `analyst` (read-only), `dpo` (audit and policy veto, no execution access). The DPO can block a program; nobody can unblock it without their recorded sign-off. Mandatory approvals are configurable per compliance tier.

Whether Zolts models seats at all is decision 50, and it gates this section, the per-rep routing capacity a programme may declare, and the MFA the security sequence promises on day one. COMP-7 sizes the role model; nothing here should be sold as present until it lands.
