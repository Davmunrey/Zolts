# 20 · The runtime

The reference core in `zolts/` decides. The runtime in `runtime/` remembers, acts and proves what it did. `zolts/` has no I/O and never imports `runtime/`; the dependency runs one way.

## What runs today

| Piece | Where | State |
|---|---|---|
| Schema, 18 tables, RLS forced on 16 | `runtime/migrations/` | Running on Postgres 16 |
| Tenant-scoped data access | `runtime/db.py`, `runtime/repo/` | Running |
| Signal ingest to enrollment, with holdout assignment | `runtime/engine/enroll.py` | Running |
| Step planning and the transactional outbox | `runtime/engine/planner.py`, `runtime/repo/actions.py` | Running |
| Policy gate at dispatch time | `runtime/engine/gate.py` | Running |
| Leased worker with backoff and dead-letter | `runtime/engine/worker.py` | Running |
| CRM contract + contract suite | `runtime/connectors/crm.py`, `tests/test_crm_contract.py` | Running |
| HubSpot and Pipedrive as CRM sources | `runtime/connectors/hubspot.py`, `runtime/connectors/pipedrive.py` | Written, not yet run against a live account |
| Smartlead sending | `runtime/connectors/smartlead.py` | Written, not yet run against a live account |
| HTTP API with API-key tenancy | `runtime/api/` | Running |
| CLI: migrate, provision, worker, serve | `runtime/cli.py` | Running |
| Console served from the API with live tenant data | `runtime/api/console.py`, `runtime/surface.py` | Running |
| Signed inbound webhooks: replies, bounces, opt-outs, deals | `runtime/api/webhooks.py`, `runtime/engine/inbound.py` | Running |
| Agent layer: propose-only, provenance-checked, eval-gated | `runtime/agents/`, `runtime/engine/generate.py` | Running |
| Cost governance before every model call | `runtime/agents/spend.py` + Trazum `spend_guard` | Running |

Not built: the enrichment waterfall wired to real providers, warehouse zero-copy, a per-tenant trained brand classifier (the deterministic floor under it ships), and any second sending channel. Those are named here so the gap is a decision rather than a discovery.

## The agent layer

A step naming an `agent:` is generated before it is sent, and generation is a different kind of action from dispatch. The order is the design:

```
generate action claimed
  └─ policy gate FIRST      drafting for a contact you may not write to spends money on nothing
     └─ dossier assembled   by retrieval; every item names its own subject
        └─ tokens counted   from the API, not estimated — you cannot authorise a call you have not sized
           └─ spend guard   Trazum prices it; a refusal names the cheaper model and the agent takes it
              └─ generate
                 └─ provenance   every claim without a source is removed
                    └─ evals     unit, factuality, brand; compliance vetoes rather than averages
                       └─ gate   approved → a dispatch action; otherwise → somebody's review queue
```

**Agents cannot send.** An agent writes a row in `proposal` and nothing else. The only two paths from generated text to a provider are the gate approving it and a person approving it through `POST /v1/proposals/{id}/approve`, and the row records which. That is ADR-012, and it is what a regulated buyer is actually asking about.

**Compliance is a veto, not a term in an average.** A missing opt-out line in a well-written message scores 0.95 and still cannot be sent. Averaging it would let a message that breaks the law clear the bar on the strength of its prose.

**An unmeasured check is not a pass.** No factuality verifier configured means no score, and no score means no unattended send. Treating an absent check as 1.0 turns every unconfigured tenant into an unattended sender.

**A program with no declared budget cannot run an agent.** The guard answers `cannot-tell`, and `cannot-tell` is not a yes.

Two defects came out of wiring it to real data. The provenance rule first measured overlap across every content word, which dropped *"Northwind opened four RevOps roles last quarter, which usually means the reporting layer is about to be rebuilt"* — with that exact fact in evidence — because the interpretive clause diluted the overlap. It now measures the citable half of a sentence: numbers, proper nouns, absolutes. And the retrieval split the company name into its own evidence item, so no single source could support a sentence that named who did the thing; each item now states its own subject, because that is what a citation is.

## Adding a CRM

`runtime/connectors/crm.py` is the whole surface. A new source maps the provider's objects onto `CrmAccount` and `CrmContact`, declares a `Capabilities`, and passes `tests/test_crm_contract.py` — which is parametrised over every registered source, so a connector cannot ship without answering its questions.

Pipedrive exists to prove the seam is not HubSpot-shaped: organizations rather than companies, an array of addresses per person rather than a field, a nested `org_id` object, offset pagination, and the credential in the query string. Nothing about it matches, and the sync did not change to accommodate it.

The contract's sharpest rule is about consent. `Capabilities.reads_opt_out` is a claim the suite verifies: a source that claims it and never reports an opted-out contact fails. A source that does **not** claim it has every contact stored with consent `unknown`, the policy gate denies on `unknown`, and the sync report says which CRM could not answer. A CRM's silence is never read as permission — which is also the reason a unified CRM API cannot be the only integration, since that is precisely the field they normalise worst.

## Connecting a CRM nobody here has seen

A partner may run their own system, built in-house, with field names nobody can guess. No connector written here can read it, so the connector stops being the unit of work and the mapping becomes it (ADR-014). The tenant publishes a YAML document; `GenericSource` reads it and passes the same contract suite as HubSpot and Pipedrive.

```yaml
apiVersion: zolts/v1
kind: CrmMapping
metadata: {provider: acme-internal, name: Acme internal CRM}
spec:
  transport:
    kind: http                       # or 'push', for a system behind a VPN
    base_url: https://crm.acme.internal/api/v2
    auth: {kind: header, name: x-acme-key}
    contacts:
      path: /people
      records: data.items
      pagination: {kind: cursor, param: after, cursor_path: data.next_cursor, size: 200}
  contacts:
    external_id: id | str
    email: emails[primary].address | trim | lower
    full_name: name_parts | join
    consent:
      field: mail_pref
      values: {opted_in: allowed, opted_out: opted_out, never_asked: unknown}
      default: unknown
```

Four path forms, and no fifth: `properties.email`, `emails[0].value`, `emails[primary].address`, and a transform pipeline `name | trim | lower`. Anything more expressive is a program, and a customer-authored program run against a customer payload inside this runtime is a liability sold as a feature.

| | |
|---|---|
| Publish | `POST /v1/crm/mappings`, or `zolts crm-mapping --tenant … --file …` |
| List | `GET /v1/crm/mappings` — provider, `spec_hash`, `reads_opt_out` |
| Pull | `zolts sync --provider acme-internal` — falls back to the tenant's mapping when no built-in source matches |
| Push | `POST /v1/crm/acme-internal/records` — refused with 409 if the mapping declares `http`, because the document would then describe one system and the runtime run another |

`reads_opt_out` is derived at publish time from whether the document names the consent field — never set by the author. Omitting the block is allowed and answered with a warning: every contact imported is stored `unknown` and unreachable. `default: allowed` with no value map is refused, because it marks an entire imported list contactable regardless of what the CRM says.

Validation runs at three doors: `scripts/validate.py` in CI, the publish endpoint, and the CLI. All three raise the same `MappingError` — a caller handling a customer's document should not have to know which library rejected it. Beyond the schema, a document is refused for an unknown transform, a malformed path segment, a consent block that would mark everyone contactable, a `base_url` on loopback or link-local (the runtime's own network position, and the cloud metadata endpoint with it), and a provider name a built-in connector already owns.

## The console

`GET /console` serves the operator surface with the tenant's live figures inlined, same origin as the API. That means no CORS to configure, no second origin in `connect-src`, and the same content security policy derivation the static build uses — `runtime/surface.py` holds both, because two copies would drift and the copy that drifts is the one that ships a policy the page violates.

The view model in `runtime/api/console.py` emits exactly the shape the static fixture emits. The console does not know whether it is looking at a demo or a tenant, which is the point: one rendering path, not two.

Rendering it against live data found two defects that no test had:

| Defect | Why it mattered |
|---|---|
| The surface crashed on a program with no measurement yet | Which is every program on day one. The view model emitted a conversion rate of 0.00 for "no outcomes", the surface's guard passed, and the next line called `toFixed` on a null lift. Null, not zero: a program with no outcomes has no conversion rate |
| It reported EUR 1.75m incremental against a control arm with zero observed conversions | The minimum detectable effect had been computed from a 1% floor rather than an estimate, so it read as precise and was not. This is precisely the failure the product exists to prevent, in the product |

The second is now a rule in the core rather than a check in a surface: `zolts.experiment.is_resolvable` refuses to declare any effect until both arms carry at least five observed conversions, which is the normal approximation's conventional floor. The MDE, the needed holdout and the pipeline are all withheld below it, and the surface states the reason rather than showing a bare dash — an operator who cannot tell "not yet" from "broken" stops trusting the screen.

The same rule now governs the demo build. One of its four programs turns out to have two conversions in its control arm, so its 10.14 pp detectable effect was never a number worth reading.

## The path a signal takes

```
POST /v1/signals
  └─ signal recorded            idempotent on dedupe_key; a replay is a no-op
     └─ live programs matched   trigger events + window, evaluated by zolts.expr
        └─ cooldown checked     dedupe.cooldown from the program
           └─ holdout assigned  zolts.experiment.assign; control is enrolled, never scheduled
              └─ tier resolved  first route tier whose predicate holds
                 └─ enrollment  unique per (tenant, program, entity) in the database

worker tick
  └─ due enrollments  →  next step queued as one action row
  └─ due actions claimed under a lease (for update skip locked)
     └─ contact resolved
        └─ policy gate      zolts.policy.evaluate; decision recorded either way
           ├─ deny  → action cancelled, touch recorded as failed, never retried
           └─ allow → connector called with the idempotency key
                      └─ touch + cost recorded, action succeeded
```

## Why the design is shaped this way

**One step is queued at a time.** Queueing a whole sequence up front commits the runtime to touches that a reply, an opt-out or a policy change should have cancelled. The cost is a worker tick per step; the benefit is that an exit rule can still stop tomorrow's email.

**The gate runs at dispatch, not at planning.** Consent, suppression and frequency all change in the window between scheduling a step and sending it — which is exactly the window in which someone unsubscribes.

**A denial is terminal, a failure is not.** Retrying a policy denial is useless at best; for a suppression it is an attempt to contact someone who asked not to be contacted. Denied actions are cancelled with the decision id attached. Transient failures back off on a schedule that starts at 30 seconds and ends at six hours, then dead-letter.

**A tenant with no connector fails loudly.** Falling back to a stub would report success while sending nothing. That is the failure mode that makes a sending platform untrustworthy, so it is a permanent error with a named reason.

**Sending is never free.** Every send records a cost in micros, because a budget rule reading zero is a budget rule that never fires.

**An inbound event is stored before it is interpreted.** A provider that changes its payload, or an interpretation this runtime gets wrong, is then a replay rather than data that never existed. The raw body is the only record of what a provider actually said, and it is the one a compliance question is answered from.

**An opt-out does all three things or none.** Suppress the address, exit the enrollment, cancel its queued work. This repository has found the same failure four times in other disguises — a contact asks not to be contacted and tomorrow's step still goes out — so the three are one transaction and a test asserts that the policy gate then denies the contact the webhook just suppressed. A suppression the gate disagrees with is decorative.

**A webhook with no signing secret accepts nothing.** An unauthenticated endpoint that records outcomes is a way for anyone who learns the URL to move a customer's measured lift.

## Operating it

```bash
export ZOLTS_DATABASE_URL=postgresql://owner@host/zolts       # owns the schema
export ZOLTS_APP_DATABASE_URL=postgresql://zolts_app@host/zolts  # cannot bypass RLS
export ZOLTS_SECRET_KEY=$(openssl rand -hex 32)               # seals connector credentials

python3 -m runtime.cli migrate
python3 -m runtime.cli create-tenant --slug acme --name "Acme" --region eu \
    --blueprint b2b-saas-sales-led
python3 -m runtime.cli issue-key --tenant <id> --name ci        # shown once
printf '%s' "$HUBSPOT_TOKEN" | python3 -m runtime.cli connect --tenant <id> --provider hubspot
python3 -m runtime.cli serve &
python3 -m runtime.cli worker
```

`docker compose up` runs the same thing with a database, migrations, API and worker.

A first run is one command:

```bash
python3 -m runtime.cli quickstart --slug acme --name "Acme Analytics"
```

It provisions the tenant, publishes and activates the four example programs, opens a webhook endpoint and issues a key, then prints the console URL. It deliberately does **not** create a connector. A tenant with no connection fails loudly on dispatch, and that is the correct first experience: the operator says which provider is theirs rather than discovering later that nothing was ever sent.

## Deploying it

| Target | File | What it gives you |
|---|---|---|
| Anywhere with Docker | `docker-compose.yml` | Database, migrations, API, worker |
| Render | `render.yaml` | Managed Postgres, web service, background worker, migrations as a pre-deploy step |
| Fly | `fly.toml` | Two processes from one image, migrations as a release command |

All three run the API and the worker from the same image, because they share the code and differ only in the command.

Two things need doing by hand on a managed host, and both are deliberate:

**Create the application role.** A managed Postgres issues one role, and it owns the schema. `ZOLTS_APP_DATABASE_URL` must point at a second role that cannot bypass row-level security — `create role zolts_app login password '…'`, then re-run `migrate`, which re-grants. Leaving it unset makes the application connect as the owner. RLS is FORCED so the policies still apply, but the second lock is gone, and `GET /health` reports `isolation_enforced: false` so the state is visible rather than assumed.

**Set `ZOLTS_SECRET_KEY` once and keep it.** It seals connector credentials before they reach the database, which is the property that matters when the database is managed by someone else. Losing it means re-entering every credential. It belongs in a secret manager, not in the database it protects.

On Fly, do not enable `auto_stop_machines` for the worker: it holds leases, and stopping it mid-flight makes recovery wait for the lease to expire rather than happen at the next tick.

`ZOLTS_SECRET_KEY` seals connector credentials with AES-GCM before they reach the database, so a dump discloses nothing on its own. Losing it means re-entering every credential; it belongs in a secret manager, not in the database it protects.

## What would break first at scale

| Limit | Bites at roughly | Fix when it does |
|---|---|---|
| Single claim query across all tenants | Low thousands of actions per second | Partition `action` by tenant hash, one claim per partition |
| One worker process per deployment | Same | The lease already makes workers horizontally safe; run more |
| `plan_due` scans every active tenant per tick | Thousands of tenants | Drive planning from a due-time index rather than a tenant loop |
| Contact-local hour approximated by country | Immediately, for accuracy | Resolve a real timezone per contact; the coarse table is marked in the code |
| One Trazum process per worker, one call per generation | Thousands of generations per minute | The verdict is a pure function of the figures passed; cache it per (model, budget bucket) |
| Misattribution across sources is not caught deterministically | Whenever a model borrows a real figure for the wrong subject | The factuality judge in `docs/08`; the regex layer catches fabrication, not misattribution, and says so |
| Provider rate limits are the connector's problem | First large tenant | A shared token bucket per (tenant, provider) in the claim path |
| Webhooks are interpreted inline on the request | A provider bursting a backlog | The events are already stored first; move the interpretation to the worker |
| A CRM sync re-crawls the whole portal each run | A portal past a few hundred thousand records | Incremental search by the provider's last-modified field; the writes are already idempotent, so only the read is wasteful |
| Two native CRMs, and the market has a hundred | The first prospect on Salesforce or Dynamics | A unified-API vendor is one more `CrmSource`, not an architecture change — ADR-013 |

None is load-bearing before the first paying customers, and each is a contained change. They are listed so that the first one to bite is a known cost rather than an outage.

## Tests

504 tests. The runtime's 151 run against a real Postgres and are skipped, never faked, when one is absent — an isolation property verified against a stub is not verified. CI fails a run that skipped them.

What they assert, in the order that matters:

1. A tenant reads only its own rows; writing another tenant's id is rejected; a query with no tenant raises.
2. A program with no declared holdout refuses to enroll, and a zero holdout needs a written justification.
3. A control enrollment is never scheduled and the planner refuses it again.
4. Every dispatched action carries a policy decision id.
5. A denied action is cancelled, not retried.
6. A redelivered action does not send twice.
7. An exited enrollment's queued work is cancelled.
8. A tenant with no connection fails loudly rather than silently.
9. No effect is declared while either arm carries fewer than five observed conversions.
10. The live view model and the static fixture carry the same shape, and both honour the contract the surface's rendering relies on.
11. An unsigned or wrongly signed webhook is rejected, and an unknown endpoint token is indistinguishable from a revoked one.
12. An opt-out arriving by webhook suppresses, exits and cancels — and the policy gate then denies that contact.
13. A retried provider event is applied once. A retried conversion would move the measured lift.
14. An agent step with no model configured fails loudly rather than sending an empty message.
15. A draft that fails the gate never queues a send, and a dispatched proposal cannot be re-approved.
16. What reaches the provider is what survived provenance, not what the model wrote.
17. A CRM that cannot read opt-out state never produces a legal basis, and the contact it returns is denied by the policy gate.
18. The sync contains no provider field names; a test greps for them.
19. A tenant-authored mapping is refused for an unknown transform, and for a consent block that would mark everyone contactable.
20. A mapping belongs to one tenant: another tenant's key lists nothing and pushes nothing.
21. Records pushed through a mapping land as the same canonical entities, with consent translated out of the CRM's own vocabulary.
