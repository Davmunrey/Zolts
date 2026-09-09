# 26 · Partner onboarding

From a name on a list to a first send with a holdout, for a design partner on HubSpot, Pipedrive or Salesforce. Every command and path on this page is checked against the code by `tests/test_partner_onboarding.py`, and the thresholds in the letter are read out of `docs/14` by the same test — a pilot criterion that drifts from the KPI page is two promises.

**What a partner is.** Paid, €5k for three months (decision B7), with success criteria written, signed and dated before the first program activates (decision B8). A free pilot produces polite feedback; a pilot without a prior criterion always "goes well" and never converts.

**The order is the point.** The baseline is frozen before anything activates, because it cannot be reconstructed afterwards (`docs/17`, ADR-042). The letter is signed before the baseline is frozen, because the letter quotes the digest of the row and the row quotes the signatory.

## The path

| Step | Who | What | Proof |
|---|---|---|---|
| 1 | Operator | Mint the invitation | the link, shown once |
| 2 | Partner | Redeem it: a tenant, a first key, starter programs as drafts | `POST /v1/signup` answers 201 |
| 3 | Both | Sign the letter (below) | a dated signature on the partner's side |
| 4 | Operator | Freeze the baseline | `zolts baseline` prints the digest the letter quotes |
| 5 | Partner | Connect the CRM | `zolts sync` reports accounts, contacts and deals |
| 6 | Partner | Connect sending: domain, mailboxes, Smartlead, the reply webhook | `zolts capacity` shows a cap above zero |
| 7 | Partner | Activate one program | `lint` is empty; `tenants can send` is ok |
| 8 | Runtime | The first send | a touch with `status = 'sent'`, and a policy decision beside it |
| 9 | Operator | Declare the period's own GTM spend, before closing | `zolts period-spend` prints the total the reports will divide by |
| 10 | Operator | Close the period | `zolts close-period` freezes one incrementality report per program, and prints each verdict and digest |

### 1 · The invitation

```bash
python3 -m runtime.cli invite --company "Northwind Traders" \
  --email revops@northwind.example --blueprint b2b-saas-sales-led \
  --base-url https://zolts.vercel.app
```

The token is shown once and stored hashed; the link expires in fourteen days. There is no HTTP route for this step: an operator capability reachable from a tenant's key is a privilege escalation waiting to be found (ADR-015).

### 2 · The partner redeems it

`GET /v1/signup/{token}` shows what the form should say without consuming anything. `POST /v1/signup` turns the invitation into a tenant, a first API key — shown once — and the blueprint's starter programs as **drafts**. Nothing contacts anybody yet. The partner opens `GET /console` and signs in with the key.

### 3 · The letter

Signed before step 4, dated, by the person on the partner's side who owns the number. The template is at the end of this page. Three things go in it that a sales conversation leaves out: the metric, the threshold, and what happens when the threshold is met — or is not.

### 4 · The baseline, frozen

What the partner's GTM cost and produced in the ninety days before Zolts. Four spend fields declared at onboarding (EUR per month, in micros), and the window's funnel — contacts made, replies, meetings, opportunities — declared, or imported from the CRM once step 5 has run. Written once; a second capture is refused.

```bash
cat > northwind-baseline.json <<'EOF'
{"window_start": "2026-06-01", "window_end": "2026-08-30",
 "spend_tools_micros": 1200000000, "spend_data_micros": 800000000,
 "spend_sending_micros": 300000000, "spend_people_micros": 9000000000,
 "contacted": 1200, "replied": 36, "meetings": 9, "opportunities": 3,
 "source": "declared"}
EOF
python3 -m runtime.cli baseline --tenant <id> --file northwind-baseline.json \
  --signed-by "R. Ortega, VP Sales"
```

The output carries the `digest`, which goes into the letter. The same capture is `POST /v1/baseline` for a partner who prefers to enter it themselves, and `GET /v1/baseline` reads it back; a second `POST` answers 409. Cost per meeting and per opportunity are derived once and stored, so the number the letter quotes is the number the row holds three months later.

```sql
select digest, source, meetings, opportunities, cost_per_meeting_micros, signed_by, frozen_at
  from tenant_baseline;
```

### 5 · The CRM

One command for all three, and the credential is read from stdin so it never reaches a shell history or a process listing.

```bash
printf '%s' "$TOKEN" | python3 -m runtime.cli connect --tenant <id> --provider hubspot --secret-stdin
python3 -m runtime.cli sync --tenant <id> --provider hubspot
```

| CRM | The credential | Configuration | What differs |
|---|---|---|---|
| HubSpot | a private-app token that can read companies, contacts and deals, and write tasks and one custom property | none | one host for everybody |
| Pipedrive | the account's API token | none | offset paging; deleted deals are skipped |
| Salesforce | an access token for the org | `--config '{"instance_url": "https://acme.my.salesforce.com"}'` | **the host is per customer**, so the credential alone cannot reach it. Tokens expire; re-entering one is `connect` again |

All three read deals, so the flagship program's open-deal exclusion is answerable from the first sync (ADR-038). The sync report says what it read and what it could not: read the caveats before step 7, because an audience that filters on deals refuses to enrol until a source that reads them has delivered some.

Anything else — an in-house CRM, a spreadsheet export — is a mapping document published with `POST /v1/crm/mappings` and pushed through `POST /v1/crm/{provider}/records`; no code per customer (ADR-014).

### 6 · Sending

Four registrations, in this order, because each one is what the next one's cap is computed from.

```bash
# The domain. Zero capacity until an operator confirms SPF and DKIM are
# published (decision 20): an unconfirmed domain cannot send, which is the
# safe failure. A burned domain takes months to replace.
python3 -m runtime.cli sending-domain --tenant <id> --name outbound.northwind.example \
  --spf --dkim --dmarc quarantine --one-click-unsubscribe

# Each mailbox, with the day its warm-up began. Today is the default, which
# is the cautious reading and sends less.
python3 -m runtime.cli mailbox --tenant <id> --address ana@outbound.northwind.example \
  --provider google --warmup-started 2026-08-11

# Smartlead, bound to one campaign. The connector refuses to send without the
# binding: guessing a campaign means guessing whose reputation to spend.
printf '%s' "$SMARTLEAD_KEY" | python3 -m runtime.cli connect --tenant <id> \
  --provider smartlead --config '{"campaign_id": "<campaign>"}' --secret-stdin

# The endpoint Smartlead posts replies, bounces and opt-outs to. The secret is
# shown once; it goes into the campaign's webhook settings.
python3 -m runtime.cli webhook --tenant <id> --provider smartlead

# What the fleet can do today. Zero here is a domain nobody confirmed.
python3 -m runtime.cli capacity --tenant <id>
```

Smartlead picks the sending mailbox itself, so the mailbox Zolts allocates is an intent recorded on the touch and the cap bounds the volume released per day (decision 19). Say so to the partner before their compliance review asks which mailbox sent what.

### 7 · Activation

The partner reads the program — it is text, in the console and at `GET /v1/programs` — and activates it: `POST /v1/programs/{program_id}/activate`. The response's `lint` must be empty. One line it can carry: *no baseline is frozen for this tenant*, which means step 4 was skipped and the lift this program measures will have nothing before it to compare against (decision 35). The starter programs declare a 10% holdout; waiving it needs a written justification stored with the program (product invariant 4).

```bash
python3 -m runtime.cli liveness      # `tenants can send` must be ok before the first tick
```

### 8 · The first send

On Vercel the cron drains the outbox within a minute; anywhere else, `python3 -m runtime.cli worker --once` drains one tick by hand and prints what it did. The proof is a touch and the decision beside it:

```sql
select t.channel, t.status, t.sent_at, d.decision, d.rule_key
  from touch t left join policy_decision d on d.subject_id = t.enrollment_id
 order by t.sent_at desc nulls last limit 5;
```

Then `GET /v1/programs/{program_id}/measurement`: `treatment` and `control` counts, the lift and the minimum detectable effect beside it, and `significant` — which stays `false` for weeks, honestly, until the arms are large enough. The console's Experiments question is deliberately not a view (`docs/20`).

### 9 · The period's own spend, declared

**Before the close, not after.** The cost per incremental meeting on every report is all in: the partner's own go-to-market spend for the period plus what Zolts billed. With no declaration it falls back to the baseline's monthly run-rate from step 4, prorated by days — an assumption that was true on the partner's first day and that nothing re-measures. A team that grew, or a tool that was dropped, moves the real figure and not that one.

```bash
cat > /tmp/september.json <<'EOF'
{"window_start": "2026-09-01", "window_end": "2026-10-01",
 "spend_tools_micros": 1400000000, "spend_data_micros": 900000000,
 "spend_sending_micros": 400000000, "spend_people_micros": 12000000000}
EOF
python3 -m runtime.cli period-spend --tenant <id> --period <period id> --file /tmp/september.json
```

Same four fields as the baseline, same units, **for the period rather than per month** — they are not prorated. Written once and never replaced, like the baseline and the report (ADR-046). Declaring is optional: skip it and the reports say they used the run-rate, which is a figure the letter can still be read against. Declaring *after* the close is refused, because the reports are already frozen and a row written then would disagree with every one of them while changing none.

The frozen report names which basis it used, so a reader never has to assume:

```sql
select period_start,
       body->>'own_spend_basis'                                as basis,
       (body->>'own_spend_micros')::bigint / 1000000           as own_spend_eur,
       (body->>'cost_per_incremental_meeting_micros')::bigint / 1000000 as per_meeting_eur
  from incrementality_report order by period_end desc;
```

### 10 · The report, frozen

At every period close — `python3 -m runtime.cli close-period --tenant <id>` — the runtime freezes the **incrementality report** for each program that enrolled anybody: the same arms and lift, the unread share, decisions, credits by kind, pipeline on opportunities only, and the baseline's digest from step 4, written once with a digest of its own (ADR-043). `python3 -m runtime.cli report --tenant <id>` prints it; `GET /v1/programs/{program_id}/reports` lists it. The one at the term's last close is what the letter's success table is read from.

```sql
select verdict, period_start, period_end, digest, frozen_at
  from incrementality_report order by period_end desc;
```

## Per-CRM checklists

| | HubSpot | Pipedrive | Salesforce |
|---|---|---|---|
| Credential | private-app token | API token | access token + `instance_url` |
| Reads opt-out | yes | yes | yes (`HasOptedOutOfEmail`; `false` is *not asked*, reported as legitimate interest) |
| Reads deals | yes | yes (deleted skipped) | yes (`IsWon` before `IsClosed`) |
| Employee count | band | band | integer, stored as an attribute with a caveat |
| Write-back | tasks | tasks | tasks |
| Re-sync cadence in the pilot | daily, by hand or by the operator's cron | daily | daily; re-enter the token when it expires |
| Watch for | a company with no domain (unmatched contacts) | a person with several organisations (first wins) | the org's API limits on a full first sync |

## The letter

> **Pilot agreement — [Company], [Country]**
>
> **Term.** Three months from the date of the first activated program, for €5k, invoiced on signature (decision B7).
>
> **Before.** The baseline frozen on [date] with digest `[64 hex characters]`, declared by [name, role]: monthly GTM spend of €[tools] on tools, €[data] on data, €[sending] on sending and €[people] on people; in the ninety days to [window end], [contacted] contacts made, [replied] replies, [meetings] meetings and [opportunities] opportunities — a cost per meeting of €[x] and per opportunity of €[y]. The row is written once and the digest can be recomputed from it by either party. Each period's *after* is read on the same basis — the partner's own spend plus what Zolts billed — and the report names whether that spend was declared for the period or prorated from this baseline, because the same figure means different things (decision 46).
>
> **What runs.** [Program key], on [blueprint], with a [10]% holdout assigned deterministically per account. The holdout is not waived. A conversion is what the programme's declared `primary_metric` counts, inside the window that metric names — for [program key], [metric] — and it is named in the frozen report and covered by its digest (decision 40), and the measurement reports how many replies were read by a person beside how many were counted (decision 16).
>
> **Success.** Measured on the treatment arm against the holdout, over the term:
>
> | Criterion | Threshold | Where it is read |
> |---|---|---|
> | Positive reply rate | >3% | the frozen report's `converted_by_type.reply_positive.treatment` over `primary.treatment_enrolled`. **Not** `primary.treatment_rate`: since decision 40 that is the rate of the programme's own declared metric, which for the flagship is opportunities |
> | Email bounce rate | <2% | the Sending view; a domain paused by a cut-off is a failed criterion |
> | Incremental lift versus holdout | >1.5×, with the minimum detectable effect reported beside it | the frozen report's `primary.lift` beside `primary.minimum_detectable_effect`, and its `verdict` — on `primary_metric`, which the report names and the digest covers |
> | Every action carries a recorded policy decision | 100% | the report's decision counts; enforced by construction |
>
> The read at the end of the term is the **incrementality report** frozen at the close of the term's last billing period — `GET /v1/programs/{program_id}/reports`, digest quoted on both copies — not a figure read off a screen on the day. A criterion that is not significant at the end of the term is reported as not significant, not as met; a criterion the sample cannot resolve is reported as not resolvable.
>
> **After.** If every criterion is met, the pilot converts to a Starter or Growth subscription (`docs/12`) on the term's last day. If not, it ends, the tenant is deleted on request, and both parties keep the frozen reports and the baseline.
>
> **Data.** Zolts processes contact data on the partner's instructions as a processor (`docs/11`); credentials are sealed at rest, and the partner may revoke every key at any time.
>
> Signed, [name, role, date] for [Company] · [name, date] for Zolts

## What this page does not cover

| Not here | Where |
|---|---|
| The commands when something breaks | `docs/25` |
| What the CRM connectors can and cannot read | `docs/20`, ADR-013, ADR-038 |
| Pricing after the pilot | `docs/12` |
| A second sending channel | decision 27: a LinkedIn step is a person's task until a partner asks |
