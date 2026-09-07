# 20 · The runtime

The reference core in `zolts/` decides. The runtime in `runtime/` remembers, acts and proves what it did. `zolts/` has no I/O and never imports `runtime/`; the dependency runs one way.

## What runs today

| Piece | Where | State |
|---|---|---|
| Schema, 33 tables, RLS forced on 29 | `runtime/migrations/` | Running on Postgres 16 |
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
| The worker as a cron-invoked function, for a host with no processes | `runtime/serverless.py`, `api/index.py` | Executed in tests; the production release waits on the founder's secrets (ADR-041) |
| Console served from the API with live tenant data | `runtime/api/console.py`, `runtime/surface.py` | Running |
| Signed inbound webhooks: replies, bounces, opt-outs, deals | `runtime/api/webhooks.py`, `runtime/engine/inbound.py` | Running |
| Agent layer: propose-only, provenance-checked, eval-gated | `runtime/agents/`, `runtime/engine/generate.py` | Running |
| Cost governance before every model call | `runtime/agents/spend.py` + Trazum `spend_guard` | Running |

Not built: warehouse zero-copy, a per-tenant trained brand classifier (the deterministic floor under it ships), and any second sending channel. The enrichment waterfall was on this list until it ran against a real provider. They are named here so the gap is a decision rather than a discovery.

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

`runtime/connectors/crm.py` is the whole surface. A new source maps the provider's objects onto `CrmAccount`, `CrmContact` and `CrmOpportunity`, declares a `Capabilities`, and passes `tests/test_crm_contract.py` — which is parametrised over every registered source, so a connector cannot ship without answering its questions.

Pipedrive exists to prove the seam is not HubSpot-shaped: organizations rather than companies, an array of addresses per person rather than a field, a nested `org_id` object, offset pagination, and the credential in the query string. Nothing about it matches, and the sync did not change to accommodate it.

**Two capability flags are load-bearing, and both work the same way.** `reads_opt_out` decides whether a CRM's silence about a contact becomes permission. `reads_opportunities` decides whether its silence about deals becomes *"this account is not in a live sales conversation"* — the exclusion that keeps outbound out of a deal the sales team is already running. Both are declared rather than discovered, both are verified by the suite (a source claiming one and returning nothing fails), and in both cases an honest gap is handled while a false claim is trusted. A source that cannot read deals leaves `crm_sync_state.opportunities_synced_at` unwritten, and a program whose audience excludes open deals then refuses to enrol rather than contacting everybody. ADR-038.

The contract's sharpest rule is about consent. `Capabilities.reads_opt_out` is a claim the suite verifies: a source that claims it and never reports an opted-out contact fails. A source that does **not** claim it has every contact stored with consent `unknown`, the policy gate denies on `unknown`, and the sync report says which CRM could not answer. A CRM's silence is never read as permission — which is also the reason a unified CRM API cannot be the only integration, since that is precisely the field they normalise worst.

### Salesforce

The third native connector, and the one that showed the contract holds: it shares almost nothing with the first two and the contract did not move (ADR-018).

```sh
printf %s "$ACCESS_TOKEN" | python3 -m runtime.cli connect --tenant … \
  --provider salesforce --config '{"instance_url": "https://acme.my.salesforce.com"}'
```

The `instance_url` is not optional and not guessable: every org has its own. Without it the sync exits naming the field rather than requesting some other org's host.

`HasOptedOutOfEmail` is a boolean, so `true` is a definite opt-out and `false` means the CRM was checked and carries none — legitimate interest, not consent. Salesforce therefore cannot report "never asked", which is a property of the field and is asserted rather than skipped. An org that keeps opt-out in a custom field or in Marketing Cloud is not read here; publish a mapping for those, and the sync report says so on every run.

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

## Onboarding a partner

Creating a tenant used to need a shell and the database URL, so every partner cost founder time. It now needs neither (ADR-015).

```sh
# The operator mints an invitation. This has no HTTP route: an operator
# capability reachable from a tenant's key is a privilege escalation
# waiting to be found.
python3 -m runtime.cli invite --company "Northwind Traders" \
  --email revops@northwind.example --blueprint b2b-saas-sales-led
```

The partner opens the link. `GET /v1/signup/{token}` shows what the form should say without consuming anything; `POST /v1/signup` turns the invitation into a tenant, a first API key and its starter programs. That is the **only** unauthenticated write path in this runtime.

| Property | How |
|---|---|
| The token is never stored | sha256 at rest, shown once, unrecoverable — the same discipline as an API key |
| Invalid, expired and redeemed are indistinguishable | One message, `this invitation is not valid`, for all three. Telling them apart tells a caller which tokens exist |
| Redemption happens once, even concurrently | The row is locked with `for update`, the tenant is created, and only then is the invitation marked redeemed and its tenant named — both in one statement |
| A half-redeemed row cannot commit | A check constraint refuses one. It caught the first implementation, which marked the invitation redeemed before the tenant existed |
| Starter programs are drafts | A tenant contacting people before anyone has read a program is not onboarding, it is an incident |
| An empty starter set says why | Seven of the eleven blueprints ship no example program. The response names what the blueprint declares instead of returning a bare `[]` |

Invitations are operator state, not tenant state — they exist before their tenant does — so the table carries `redeemed_tenant_id` rather than `tenant_id`, and the role serving tenant requests has its access revoked.

## Opening the console

The console authenticated by the `x-api-key` header, which a browser cannot send when somebody types the URL. It answered 401 to the person it was built for, and the quickstart's own instruction was to `curl` it — which renders the page and can click nothing on it. Every check passed; nobody had opened it.

An operator now goes to `/console`, gets a sign-in page, and pastes the key they were given at signup.

| | |
|---|---|
| The cookie is not the API key | A key is a long-lived bearer credential shown once. In a cookie it is in browser storage, in history, and on every request to this origin forever. The session token is separate, 12 hours, revocable |
| Revoking a key ends its sessions | Otherwise revocation stops the credential and leaves the browser holding a working door |
| A cookie write carries a CSRF token | The cookie is `SameSite=Strict`; the double-submit token is the second lock and costs a header. Requests carrying `x-api-key` are exempt — nothing ambient sent them |
| A session cannot widen a key | It carries the scopes of the key that opened it |
| The sign-in page's CSP hash is derived from the bytes served | A hash written by hand drifts, and the failure is a page whose script silently never runs |

**Drafts are programs.** Signup publishes starter programs as drafts on purpose, and the console rendered only live ones — so a partner who had just signed up opened it, saw nothing, and had no way to activate the one thing they held. The surface had been built against a fixture in which everything was already live.

**One flag separates the two pages.** The static build inlines a fixture and keeps `connect-src` at `'none'`, so its buttons stay inert; the served view model sets `live`, and only then are the actions wired. A button that silently does nothing is worse than no button, and both pages render from one file.

### The review queue

Agents propose and the runtime disposes — and the disposing was `curl`. The rail counted a review queue that led nowhere, which makes product invariant 1 a claim with no surface behind it.

The queue now renders each waiting draft beside **what every gate said about it**, read from the row rather than recomputed: thresholds move, and the question an audit asks is what was true when it was decided.

| Shown | Why it is on the screen |
|---|---|
| The draft | What would be sent, not a summary of it |
| Removed before sending | The sentences provenance struck. A reviewer sees the difference between what the model wrote and what survived |
| Gate reason | Why a person is being asked at all — an eval below threshold, a failed required check, a spend refusal |
| Eval score and failed checks | The number and the named checks, not a verdict |
| Spend verdict and cost | What the call cost and whether the guard allowed it |
| Evidence | Every item, with its source, so a claim can be traced to what supports it |

Approving queues an action. It does not send: the policy gate still runs on the action, and a denial still cancels it. The copy on the screen says so, because a reviewer who believes Approve means Send will approve differently.

`scripts/browser_console.py` drives a real browser against a real server against a real database: type the URL, get the door, paste the key, click Activate, open the review queue, click Approve — then check in Postgres that the program is live and the proposal decided. It runs in CI.

## What a play can say

Two things a program declares, both versioned configuration and neither a new language (ADR-019).

**A step may branch on what the contact did.**

```yaml
- step: email_2
  channel: email
  wait: 4d
  when: not engagement.has_replied
- step: email_3
  channel: email
  wait: 6d
  when: engagement.no_response
```

`engagement` is read from touches and outcomes rather than from a counter, and carries `has_replied`, `has_opened`, `no_response`, `opened`, `replied`, `bounced`, `sent` and `converted`. The expression language is the one triggers and exit rules already use.

`no_response` is not `not has_replied`. Flattening them sends a breakup to somebody who is reading.

**A program may declare when its steps land.**

```yaml
schedule:
  send_window:
    days: [mon, tue, wed, thu, fri]
    opens: "08:00"
    closes: "18:00"
    timezone: Europe/Madrid
```

This moves a send; it never cancels one, and never moves one earlier. The window is the customer's local time, so it does not drift across daylight saving. A program that declares none lands wherever its waits land, exactly as before.

## Reading a reply

Every reply used to be recorded as `reply_positive`. Somebody writing "take me off your list" was counted as a conversion, left contactable, and folded into the reported lift (ADR-016).

The triage agent classifies the text and stops there — it does not suppress, does not record an outcome, and does not decide whether it is confident enough.

| Verdict | What the runtime does |
|---|---|
| `unsubscribe` | The same suppression path a provider's unsubscribe event takes |
| `negative`, `wrong_person`, `not_now` | Recorded as `reply_<verdict>`, which is not a conversion type |
| `positive` | Recorded as `reply_positive`, which is |

**A verdict must quote the reply**, and one whose quote is not in the text is discarded. A confident label with nothing behind it reads exactly like a correct one, and this decides whether somebody is contacted again.

A blocked verdict is not a weaker signal, not a reason to fail the webhook, and not a default. Absent a usable one — agents off, no model, no body in the payload, the spend guard refusing — the reply is recorded as `reply_positive`, exactly as before. That over-counts, and flipping it would move every tenant's measured lift on a deploy, silently.

**So it is counted rather than hidden.** Every outcome records whether anybody read the words behind it, and the measurement shows *conversions read of conversions total* beside the lift — on the same screen, not in a footnote, because a caveat nobody reaches is not a disclosure. A program whose number rests on 62% unread replies says so where the number is read. The share falls on its own as providers send bodies and tenants enable the agent layer: nothing to migrate, nothing to announce. Decision 16, closed.

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
| **Vercel** — the decided path | `vercel.json`, `api/index.py`, `.github/workflows/deploy-vercel.yml` | The API as one function, the worker as a cron, Neon for Postgres; production released by the workflow that migrates and preflights first (ADR-041) |
| Anywhere with Docker | `docker-compose.yml` | Database, migrations, API, worker |
| Render | `render.yaml` | Managed Postgres, web service, background worker, migrations as a pre-deploy step |
| Fly | `fly.toml` | Two processes from one image, migrations as a release command |

The three container targets run the API and the worker from the same image, because they share the code and differ only in the command. Vercel runs the same app as a function and the same worker as a schedule, built by the same `from_settings`, so there is one worker whichever host runs it.

### Vercel plus Neon, end to end

The worker has no process to be. `Worker.tick()` is one bounded pass, so Vercel Cron invokes `/api/tick` once a minute and the function drains until the outbox is empty or fifty seconds are spent; an invocation the platform kills leaves actions leased, and the lease expiring is the recovery. The route refuses any caller without the cron's bearer, and refuses to run at all when no bearer is configured.

```sh
# 1. The database. Create the Neon project in eu-central-1: the function runs
#    in Frankfurt (`regions` in vercel.json) and the Atlantic costs 90 ms a
#    query. Take the DIRECT endpoint, not the one with -pooler in the host.

# 2. The second role, the one that serves requests. Run against the owner URL:
psql "$OWNER_URL" -c "create role zolts_app login password '…'"

# 3. Secrets, in two places, and never in the repository, a chat or an issue.
#    GitHub → Settings → Secrets and variables → Actions → Secrets:
#      VERCEL_TOKEN            from vercel.com/account/tokens
#      VERCEL_ORG_ID           the team id, team_…
#      VERCEL_PROJECT_ID       the project id, prj_…
#      ZOLTS_DATABASE_URL      postgresql://owner:…@ep-x.eu-central-1.aws.neon.tech/zolts?sslmode=require
#      ZOLTS_APP_DATABASE_URL  postgresql://zolts_app:…@ep-x.eu-central-1.aws.neon.tech/zolts?sslmode=require
#      ZOLTS_SECRET_KEY        openssl rand -hex 32
#    …and one Variable:  ZOLTS_URL = https://zolts.vercel.app
#    Vercel → Project → Settings → Environment Variables, for Production:
#      the same three ZOLTS_* values, ZOLTS_ENV=production, and
#      CRON_SECRET             openssl rand -hex 32; the platform sends it as the bearer

# 4. The plan. A cron every minute and a five-minute function are Pro features.
#    On Hobby the deploy is refused, which is the right failure.

# 5. Release. Push to main, or run the deploy-vercel workflow by hand. It
#    migrates, runs preflight in production mode, deploys, and opens the URL.
#    Vercel's own deploys of main are off (vercel.json), so nothing goes
#    around it. With only the VERCEL_* secrets set it releases the demo and an
#    unconfigured runtime that answers 503 on every path, and says so.

# 6. Open it the way the internet does, with the cron's secret in the
#    environment so one tick runs. `worker ticking` is ok within a minute of
#    the release, or the cron is not firing.
read -rs CRON_SECRET && export CRON_SECRET     # typed, not pasted into a command line
python3 scripts/smoke_deployed.py --url https://zolts.vercel.app --strict \
    --expect-migrations runtime/migrations
```

From then on the same smoke runs every hour from the workflow, strictly, and a failed run notifies the repository owner. It is the monitor until a real one is pointed at `/health/liveness`.

### Fly plus a managed Postgres, end to end

The database is deliberately not Fly's. `fly postgres attach` sets `DATABASE_URL`, which this runtime does not read: a database URL is named explicitly or it is absent, because a runtime that picks up whichever connection string happens to be in the environment will one day pick up the wrong one.

```sh
# 1. The database. Neon's console gives you an owner connection string.
#    Take the DIRECT endpoint, not the one with -pooler in the host: this
#    runtime pools client-side, and a pooler in front of a pool buys nothing.

# 2. The second role, the one that serves requests. Run against the owner URL:
psql "$OWNER_URL" -c "create role zolts_app login password '…'"

# 3. Secrets. Never in the repository, never in the database they protect.
fly secrets set \
  ZOLTS_DATABASE_URL="postgresql://owner:…@ep-x.eu-central-1.aws.neon.tech/zolts?sslmode=require" \
  ZOLTS_APP_DATABASE_URL="postgresql://zolts_app:…@ep-x.eu-central-1.aws.neon.tech/zolts?sslmode=require" \
  ZOLTS_SECRET_KEY="$(openssl rand -hex 32)"

# 4. Deploy. The release command migrates, then runs preflight, and a
#    non-zero preflight aborts the release before any traffic reaches it.
fly deploy
```

Three values need credentials nobody but the account owner has, and each belongs in `fly secrets`, never in a file, a chat message or an issue: the two database URLs and the secret key.

```sh
# 5. Open it the way the internet does. The release command proves the runtime
#    may take traffic and `smoke_runtime.py` proves the loop against a
#    database; neither of them opens the URL, and that gap already shipped a
#    console nobody could open.
python3 scripts/smoke_deployed.py --url https://zolts.fly.dev
```

It needs no credentials: `/health`, the liveness signals, and that `/console` returns a door a person can act on with a Content-Security-Policy on it. Pass `--key` to add a tenant-scoped read, which is the difference between the process being up and the product answering. Liveness signals are reported rather than fatal — a freshly deployed instance legitimately has a tenant with no connector yet, and a smoke that fails on that trains whoever runs it to ignore the output. `--strict` is for the run after the deployment is configured.

### Preflight

`python3 -m runtime.cli preflight` answers, against the database actually connected, whether this deployment may take traffic. It is the release command on Fly, the pre-deploy step on Render, and the second step of the release job on Vercel, so a misconfigured runtime fails the deploy instead of serving.

| Check | Blocking in production when |
|---|---|
| Secret key | It is a value published in this repository, or shorter than 32 characters |
| Database | It cannot be reached — and nothing below is then reported, because a cascade of failures hides the one that matters |
| Encryption in transit | The connection is unencrypted **and** the database is not local. A loopback or Unix-socket connection needs no TLS, and calling that a failure makes the check noise |
| Application role | `ZOLTS_APP_DATABASE_URL` is unset, or the role it names is a superuser or holds `BYPASSRLS` |
| Migrations | Any migration on disk is not in the ledger |
| Forced row-level security | Any table with a `tenant_id` lacks `FORCE` — `ENABLE` alone exempts the owner |
| Unscoped reads | A query with no tenant returns rows instead of raising |
| Dry run | `ZOLTS_DRY_RUN` is true. Correct for a rehearsal, silent for a launch |

Outside production the same checks run and report as warnings, because a developer with a local database is not misconfigured.

### A transaction pooler

Neon, Supabase and RDS Proxy all offer a pooled endpoint, and Neon's console offers it first. The runtime recognises one (`-pooler.` in the host, `pgbouncer=true`, or port 6543) and disables prepared statements on those connections. psycopg names a prepared statement after the fifth execution of a query, and a transaction pooler hands the next transaction a different backend that has never seen that name — so an unadapted deployment works for a few minutes and then fails under exactly the load that made it worth deploying.

Tenant scoping is unaffected either way: `set_config('zolts.tenant_id', …, true)` is transaction-local and cannot outlive the transaction that set it, whichever backend runs it.

Two things need doing by hand on a managed host, and both are deliberate:

**Create the application role.** A managed Postgres issues one role, and it owns the schema. `ZOLTS_APP_DATABASE_URL` must point at a second role that cannot bypass row-level security — `create role zolts_app login password '…'`, then re-run `migrate`, which re-grants. Leaving it unset makes the application connect as the owner. RLS is FORCED so the policies still apply, but the second lock is gone, and `GET /health` reports `isolation_enforced: false` so the state is visible rather than assumed.

**Set `ZOLTS_SECRET_KEY` once and keep it.** It seals connector credentials before they reach the database, which is the property that matters when the database is managed by someone else. Losing it means re-entering every credential. It belongs in a secret manager, not in the database it protects.

**Replacing it is a three-step operation, not an edit.** A key that leaks and cannot be replaced is a permanent compromise of every customer's CRM, so the replacement path is built and tested (ADR-039):

```bash
# 1. The new key seals; the old one still opens. Nothing is re-sealed yet and
#    nothing breaks — every credential in the database opens under one of them.
fly secrets set ZOLTS_SECRET_KEY="$(openssl rand -hex 32)" \
                ZOLTS_PREVIOUS_SECRET_KEYS="<the old key>"

# 2. Re-seal. Resumable and idempotent: run it again if it is interrupted.
python3 -m runtime.cli rotate-key            # --check reports without writing

# 3. Retire the old key. This step is the rotation; until it happens the old
#    key is still live, which is what you were replacing.
fly secrets unset ZOLTS_PREVIOUS_SECRET_KEYS
```

`preflight` reports the state on every deploy: how many credentials are sealed under the current key, how many are still on a previous one, and — fatally in production — how many are sealed under a key this deployment does not hold at all.

On Fly, do not enable `auto_stop_machines` for the worker: it holds leases, and stopping it mid-flight makes recovery wait for the lease to expire rather than happen at the next tick.

On Vercel the same three steps are `npx vercel env add ZOLTS_PREVIOUS_SECRET_KEYS production` (it prompts for the value), `rotate-key` from anywhere holding the database URLs, and `npx vercel env rm ZOLTS_PREVIOUS_SECRET_KEYS production`, each followed by a release so the function reads the change.

`ZOLTS_SECRET_KEY` seals connector credentials with AES-GCM before they reach the database, so a dump discloses nothing on its own. Losing it means re-entering every credential; it belongs in a secret manager, not in the database it protects.

## What the image ships

The image once carried the code and none of the data the code reads. It built,
started, answered `/health` with `"status": "ok"`, seeded a tenant with zero
programs and reported success, and `/console` returned 500. Nothing failed —
the product was simply empty, and the quickstart handed a first-time operator a
URL that 500s.

Two fixes, because either alone leaves the failure mode:

| | |
|---|---|
| The image ships what the runtime reads | `examples/schema`, `examples/programs`, `blueprints`, `design`, alongside the code |
| A missing directory is an error with a name | `load_blueprints` and `load_catalog` raise on an absent directory; globbing one returns nothing, which reads as "this product has no blueprints". An empty directory still returns `[]` — absent and empty are different facts |
| `quickstart` refuses to seed nothing | A first run that publishes no programs is a failure, not a result |
| `/console` names the missing file | 503 with the path, not a stack trace |

Two checks keep it that way. `tests/test_packaging.py` resolves every data path
from the modules themselves and asserts a `COPY` ships it, so renaming a
directory and forgetting the Dockerfile fails the suite. And CI builds the image
and runs it: migrate, quickstart (asserting the programs are non-empty), then
`/health` and `/console` over HTTP against a container.

## Billing what a tenant uses

`cost_event.billed_credits` existed from the first migration and no caller ever set it. `docs/12` prices eight actions and four plans in full, and the runtime billed nothing (ADR-017).

`zolts/billing.py` holds the price list; `runtime/metering.py` holds which period a tenant is in and what they have spent. What something costs is arithmetic, when and to whom is a database.

| | |
|---|---|
| Meter | Every send and every generation, priced at the moment it happens — not conditional on knowing our own COGS |
| Ceiling | `tenant.credit_ceiling`, null meaning the plan's allowance. Checked **before** the spend and before anything about the particular action. A tenant who has run out is held, not cancelled: the action returns to pending, due when the period turns |
| Alert | 80% of the ceiling, per `docs/18` I6. A warning, not a stop — a customer who finds out at the ceiling found out too late to do anything but stop |
| Period | One open period per tenant, enforced by a partial unique index. Its terms are copied in at open time, so a mid-month upgrade does not restate the month being consumed |
| Raise | `zolts set-terms --tenant … --credit-ceiling 80000` lets a tenant spend past the plan; `--credit-ceiling plan` puts it back. Terms that cannot be billed — an enterprise ladder with a gap in it — are refused inside the transaction, so nothing is half-applied |
| Close | `zolts close-period --tenant …` produces a statement. Idempotent: closing twice returns the first one rather than restating it |
| Read | `GET /v1/billing/current` for consumption, what is left, and what the period costs so far; `GET /v1/billing/statements` for closed periods |

Seats are counted from live API keys at close time rather than from a number somebody maintains, because a seat count nobody maintains undercharges forever.

**Overage and seats are charged on the ladder `docs/12` already published**, and were not, for as long as credits were not (decision 17). Additional credits are priced graduated over the overage alone — 0-100k at €0.010, 100k-500k at €0.008, above at €0.006 — and additional seats at €90/month. A test re-reads every one of those numbers out of `docs/12`, and another asserts the property that picks graduated over flat: **the bill never falls as consumption rises**. An enterprise contract overrides the ladder and the seat price in `negotiated_terms`; a ladder with a gap or a ceiling in it is refused on the way in rather than on the way to an invoice.

Overage was priced and unreachable until the ceiling became raisable. The runtime stopped every tenant at their included credits, so no tenant could consume a credit past their plan and the expansion revenue `docs/12` calls the first driver of NRR was arithmetic nobody could run. `credit_ceiling` defaults to null — the plan's allowance, the behaviour that was already there — and raising it is provisioning, like changing a plan.

## What a channel is

Adding a second channel is not adding a connector. `runtime/channels.py` holds the three things the connector contract does not: what a touch costs, what bounds how many go out, and whether this runtime performs the channel itself.

| Channel | Costs | Bounded by |
|---|---|---|
| `email` | `email.send`, 1 credit | The mailbox fleet: a daily cap and a reputation |
| `task` | Nothing extra — `docs/12` prices no such action, and the step that created it already cost 0.2 | Nothing |
| `crm` | Nothing extra, same reasoning. A sync that billed per record would make an integration the most expensive thing a customer connects | Nothing |

Anything else — `linkedin`, `ads`, `sms`, `voice` — is a step for a person. The planner reads the definitions rather than a second list of its own, so the set that decides "dispatch this" and the set that decides "here is what it costs" cannot disagree.

Three defects came out of writing this down rather than assuming it. Every dispatch was priced at `email.send`, so a HubSpot task was billed as an email the customer never sent. Every dispatch allocated a mailbox seat, so that task was held once the mailboxes were full. And `linkedin` was dispatchable with nothing behind it, so the flagship program's LinkedIn steps queued, failed permanently and were cancelled one at a time while the sequence carried on (ADR-027).

## The view chrome

Every view declares its own columns, header labels, footer and summary panel. It used to declare none of them: five views were added against the Programs header, so Spend, Prospects, Signals, Policy and Audit each labelled their own data **PROGRAM · BLUEPRINT · ENROLLED · LIFT · HOLDOUT · P95**, in a grid sized for a different table. Values wrapped, rows grew into each other, and on the prospect list an email ran into a phone number.

The header and the rows now read one custom property, so a view cannot label a column its rows do not fill. Every cell is one line with the full value on its `title`. The aggregates that used to be crammed into the list's first row — where they were neither a row nor a heading and collided with both — are the right-hand summary, which also stops that column being a void on every view except Programs.

Three defects came out of opening it in a browser rather than reading it:

| | |
|---|---|
| `.st.warn` and `.st.deny` were never written | Every amber and red status dot rendered as **nothing**, since the day those views shipped — including the mailbox in `complaint.alarm`, the one row the Sending view exists to show |
| `.seg` was never written | Six jurisdiction buttons rendered as one run of letters, `CADEESFRGBUS`, in the middle of a heading |
| Prose sat in the `dd` column | `.props dd` is `nowrap`, so a sentence printed straight over its own label |

The review queue's own panel had the third of these: the reason a person is being asked was a sentence in the `dd` column, so *"eval 0.81 below the 0.85 auto-send threshold"* printed over the word **Gate** and ran off the panel — on the screen whose entire job is telling a person why they are being asked. Reasons are prose now and measurements are values, which is what they always were.

They share a shape: a name used in one place and defined in another, with nothing holding the two together. `tests/test_console_surface.py` holds them together now — it reads the view registry out of the script and asserts each view labels exactly the columns it has, and that every class the markup uses has a rule of its own. Each assertion was verified by breaking the thing it guards and watching it fail.

`scripts/browser_console.py` measures the rest, on every view rather than three, and **exits non-zero** on either: a row taller than its declared height means a cell wrapped; a panel element wider than its box means a value is clipped or printing over its label. Neither is a matter of taste.

The Programs list also shows what the blueprints promise and no program file implements — 25 plays across 11 blueprints, computed by `Catalog.gaps()` and read by the served console and the demo alike, so a list of four programs in a column built for hundreds no longer reads as a product with four programs.

## How the screen is laid out

At 1920 the console showed four mailboxes across three separate voids: a table stretched to 1,360px with a dead tail, a 340px panel holding two short blocks, and a rail running 650px of nothing between its last entry and the keyboard hints. The information on screen occupied about eight per cent of it.

| | |
|---|---|
| **A stat row at the top of every view** | The numbers a view is about, where the eye lands, in the width a wide screen was wasting. They were in the 340px side panel, under a table that had already used the room. Values carry proportional figures rather than `tabular-nums` — at 23px every digit the width of a zero reads loose; tabular is for columns that must align |
| **A meter where there is a limit** | Ceiling used, capacity used, share denied. The fill carries the severity and the track is a dim step of the same ramp, so the state reads across the whole bar. The share is clamped, because a fill wider than its track states a different number than the one beside it |
| **The table keeps its ruling below the last row** | A short list reads as a table that ended rather than a hole in the page |
| **The table stops at a readable width** | The surplus goes to the panel, which is where the density is: `clamp(340px, 25vw, 470px)` |
| **The rail says what the console is reading** | Demo, or the tenant and its region. The first question when a number looks wrong is whether it came from anywhere, and the page never said |

### On a phone

Below 820px the rail was `display:none`. A phone landed on Programs with no way to reach the other seven views, and the eight-column grid clipped four of them off the right edge without offering a horizontal scroll: **the console was one screen with half a table on it.**

The rail is a scrolling strip of the same destinations, with their labels and counts — a row of bare icons is a quiz. Each row becomes stacked label/value pairs, from the same header spec the desktop columns are named by, so a column cannot be labelled one thing across and another down. The page scrolls naturally instead of being pinned to `100vh`, which also puts the detail panel back: below 1180px it stacks under the list rather than disappearing, and on the review queue that panel *is* the screen.

`browser_console.py` drives the same run at 390px and fails on an unreachable rail, a clipped cell, or any horizontal overflow. Both were verified by reintroducing the defect and watching the run exit non-zero.

The stat row folds to two columns below 1560px and one below 900px — before four tiles can clip, because a truncated unit changes what a number means. `scripts/browser_console.py` measures that: it fails on any element wider than its box, and on two views showing the same tiles, which is what a view that forgets to set its own would do.

## Proving it: policy and the audit log

Two screens for the two questions a regulated buyer asks first, and neither answer was reachable without a terminal.

**Policy** is grouped by rule, not by contact. One contact denied once is a correct denial; one rule denying four fifths of a program is a program to fix, and only the second grouping shows that. The rail counts *denials* rather than decisions: the total includes every allow and never moves, while the number an operator acts on is the work the engine stopped. A tenant at zero denials has either a clean list or a policy that is not running, and in a count of sends those look identical.

**Audit log** answers *who*, not *what*. Issuing, rotating or revoking a key, publishing or activating a program, approving a draft — each writes a line, and it has since the first key was issued. It was readable only from a SQL prompt, which makes it evidence nobody can produce during the diligence it exists for. `GET /v1/audit` returns it; the entries carry key ids and never a token, because a token is shown once at creation and this table is read by people who did not create the key.

Both were removed from the rail in ADR-023 rather than left as links that did nothing, with the debt recorded. This pays it (ADR-026).

## The research dossier

`docs/08` gives the Researcher a role and `docs/12` prices its output at 20 credits — the most expensive action in the list, and the last one the runtime could not execute. The word "dossier" appeared once in the code, as a local variable inside the copywriter holding evidence for one email.

```
research requested
  └─ evidence assembled   the account, its contacts, its signals, and what has already been tried
     └─ nothing known?    refused before a model is called — 20 credits buys no invention here
        └─ current one?   served from storage, billed nothing: freshness is a comparison, not a clock
           └─ budget      checked before the call; the largest single purchase in the product
              └─ generate
                 └─ provenance   every claim without a source is struck out — and kept, and shown
                    └─ stored    with what it saw, so the next request knows whether it still holds
```

`built_through` is the newest signal the dossier read. A funding round arriving afterwards makes it stale with nobody having to remember to invalidate anything; an account that has done nothing for a month keeps the answer it has. `force` rewrites a current dossier and pays again, because an operator who does not believe one must be able to say so.

A refusal — no evidence, no budget, no model — is stored and never billed. The next caller learns why without paying to find out, and it is visible that the runtime declined rather than failed.

Struck-out claims are returned rather than hidden: a dossier that lost a sentence to the verifier is a document with a hole in it, and the hole is the finding. The prospects view shows which accounts have one and reports coverage beside staleness, because coverage alone lies (ADR-025).

```sh
python3 -m runtime.cli research --tenant … --limit 5 --dry-run   # what it would cost
python3 -m runtime.cli research --tenant … --limit 5             # 20 credits each
```

## Charging for the step

`docs/12` prices program execution at 0.2 credits. It is the highest-volume action in the price list and the runtime billed none of it, so the base unit of the product's consumption was free: a six-step play over ten thousand accounts is twelve thousand credits nobody invoiced.

The price list says *execution*, and that word decides where the meter goes. The planner queues one step at a time and an exit rule cancels what is still pending, so a step billed at the queue is a step a reply or an opt-out correctly threw away — charged for. It is billed when the action reaches `succeeded`, and that single condition gives the four cases without a branch having to remember any of them.

| Disposition | Charged |
|---|---|
| Sent, generated, or handed to a person | 0.2 credits — a manual step is not a no-op; creating the task was the job |
| Refused by the policy gate | Nothing. Charging for it would make the safest configuration the most expensive one to run |
| Held for budget or capacity | Nothing. A tenant out of credits has done nothing wrong |
| In the holdout | Nothing, and it never reaches the outbox at all |

The mark is `action.billed_at`, on the step's own row: the action already carries the idempotency key that makes the step unique within its enrollment, so at-least-once execution cannot become at-least-once billing. An auto-sent email step therefore costs 1.2 credits — 0.2 for the orchestration and 1 for the send — which is what `docs/12` lists as two lines and the runtime charged as one (ADR-024).

## The console an operator sits in front of

Three views worked — Programs, Review queue, Sending — and the rail offered five more that did nothing. A nav entry with no surface behind it is worse than an absent one: it teaches an operator that clicking things here is pointless. Worse still, `/v1/accounts` and `/v1/people` were **POST-only**, so a tenant could create a prospect and had no way to read one back; the list they would live in was not unbuilt, it was unbuildable.

| View | What it answers |
|---|---|
| **Prospects** | Every account and contact, and what is missing on each. "Missing" is decided by the same rule the enrichment path uses to spend money, so the screen cannot offer to buy a field the engine will decline. An opted-out contact is shown as denied rather than hidden |
| **Signals** | What fired, and the latency split: how long the source took to notice against how long this runtime took to act. A bad total is one or the other and they need opposite fixes |
| **Spend** | Credits consumed, what is left, the share of the ceiling and where it went by kind. `GET /v1/billing/current` had answered this since billing existed and no screen asked it |

`POST /v1/enrich` takes named ids rather than "everything unresolved": it spends a data budget, and an endpoint whose cost depends on how much happened to be missing is one nobody can predict the bill for. It requires a write scope for the same reason.

Experiments, Policy and Audit log were removed from the rail rather than left as links. Policy and Audit log are back, with screens rather than links (below). Experiments is not: the data behind it needs a conclusion the measurement is often right to withhold, and a view that renders "not significant" as a number is worse than no view.

## Going and looking for a signal

`docs/06` calls signal-to-action latency the highest-leverage variable in the whole system. Nothing watched anything: signals arrived only when a customer pushed one, so the runtime's SLA was a claim about somebody else's work (ADR-022).

| | |
|---|---|
| Define | `examples/signals/*.yaml` carry the strength, half-life, legal basis, freshness SLA, refresh interval and dedupe window `docs/06` specifies. `scripts/validate.py` refuses a definition missing any of them, and fails a program that triggers on a signal nobody defined |
| Want | Only signals a **live program** declares are watched. The catalogue is the menu; the live programs are the order |
| Check | `zolts watch --tenant …` asks each source about the accounts due a look. Billed once per account **per day**, however many signals asked — which is what makes a one-hour refresh on a Tier A signal affordable |
| Refuse | A detection older than its freshness SLA is not ingested, and is counted as stale. A source that keeps finding things too late is one to replace, and that only shows up if the staleness is recorded |
| Report | `zolts latency --tenant …` splits time-to-touch into detection (how long the source took) and execution (how long we took). A bad total is one or the other, and they need opposite fixes |

A source that errors records nothing and bills nothing: the check history is what the refresh clock reads, so an outage writing "checked, found nothing" would silence the signal until the window passed again.

## Buying a missing field

`docs/12` prices eight billable actions and two of them ran. The three enrichment actions are the bulk of the consumption the pricing model assumes, and `zolts/waterfall.py` — the optimiser `docs/07` calls the margin lever — was imported by nothing (ADR-021).

| | |
|---|---|
| Order | `runtime/enrichment.py` asks `zolts.waterfall` which provider is cheapest for this cohort and stops on the first hit. The premium provider is not called for a field the cheap one found |
| Learn | Every attempt is recorded. Below 30 calls in a cohort a provider is scored on its registered default; above it, on its own record. The matrix is the asset `docs/15` says a competitor cannot buy |
| Absorb | A miss costs us and is not billed. An error is not a miss: a 401 raises rather than teaching the optimiser that a broken integration has poor coverage |
| Defend | Every resolved value records the provider, the confidence and the legal basis. `enrichment.provenance()` answers the DPO's question from the same rows the matrix is built from |
| Refuse | A value that already belongs to another person is not written. Three colleagues and one shared address merges two identities, which is worse than an empty field |

```sh
zolts data-provider --tenant … --key acme-data --fields email,phone   --cost-micros 28000 --mapping examples/providers/example-enrichment.yaml   --connection <connection-id>
zolts enrich --tenant … --field email --limit 50 --dry-run
zolts hit-rates --tenant …
```

A provider registered with a `--mapping` needs no connector written: the document is the integration, the same decision the CRM mappings make and for the same reason — a per-field abstraction that requires an engineer before it can be exercised arrives a sprint after the provider shock it exists to absorb.

**Enrichment is never automatic.** Buying data mid-send would spend a budget nobody authorised; decision 22 holds that open with a default of no.

## Sending capacity, and what stops a send

`zolts/deliverability.py` modelled warm-up curves, reputation factors, the `docs/09` thresholds and per-provider segregation since the reference core existed. Nothing in the runtime imported it, and the `mailbox` table had columns for warm-up and four rates that no code ever wrote or read (ADR-020).

| | |
|---|---|
| Derive | Every rate comes from the touches a mailbox actually sent, over a 30-day window. The four stored rate columns were dropped: a stored rate is right when written and wrong from then on, and a stale 0.0% bounce rate looks exactly like a healthy mailbox |
| Allocate | `runtime/fleet.py` picks a mailbox for the recipient's provider, most headroom first. Nothing left is a hard stop, and the action is **held** until the cap resets — not failed, not cancelled |
| Fail closed | A domain nobody registered, or one without SPF, DKIM, a DMARC policy and one-click unsubscribe, has zero capacity. Missing authentication is mail filtered on arrival, not a reputation problem to recover from |
| Break | A complaint rate at 0.3% pauses the **domain**, including its healthy mailboxes. A bounce rate at 3% pauses the **program**. Checked on the event, because a threshold evaluated overnight lets a bad afternoon finish |
| Report | `zolts capacity --tenant …`, a Sending view in the console, and a `sending domains` liveness signal. A paused domain is the most expensive silent state in the product |

Register a fleet with `zolts sending-domain --tenant … --name outbound.example --spf --dkim --dmarc quarantine --one-click-unsubscribe` and `zolts mailbox --tenant … --address ae@outbound.example --warmup-started 2026-09-01`.

**A tenant with no registered domain is reported, not blocked.** The provider owns those mailboxes and this runtime cannot cap what it has not been told about; refusing to send would stop a tenant for a reason no operator could act on. Every surface says capacity is unmanaged rather than showing a healthy zero.

**What the gate buys depends on the provider.** Smartlead takes a recipient and picks the mailbox itself, so there the allocated mailbox is an intent recorded on the touch and the gate bounds the volume released per day. Decision 19 registers that rather than implying a per-mailbox guarantee no provider agreed to.

## Operating it once a partner is real

`/health` answers "can I reach the database". That is nearly always yes, including on the morning the worker died at 3am, the outbox has been growing for six hours and a paying partner's campaign has sent nothing. Both states report `"status": "ok"`, which makes the endpoint an alibi rather than a signal.

`GET /health/liveness`, and `python3 -m runtime.cli liveness`, answer whether the deployment is doing its job. **A failing signal answers 503**, because what a monitor reads is the status code — for eleven months this endpoint answered 200 with `draining: false` in the body, so an uptime check pointed at it, exactly as this sentence instructs, saw a healthy deployment while the outbox was stalled (D-36). Fly's own check probes `/health`, not this path, so a stalled outbox does not restart the API. What to do about each signal is `docs/25`.

| Signal | Fails when | Why it is silent otherwise |
|---|---|---|
| Outbox draining | Work is due and nothing has claimed it for 30 minutes | No request errors. The queue simply grows |
| Actions completing | 10 or more actions exhausted their attempts in 24 hours | One is a bad address; each retry is logged individually and nothing looks at the pattern |
| Connections healthy | A connection is in `error` | Every send through it fails, and each failure looks like an ordinary retry |
| Tenants can send | A tenant has live programs and no working connection | The shape of an onboarding that stopped halfway: programs activated, connector never connected. It enrolls, plans, and sends nothing |
| Sending domains | A circuit breaker paused a domain | Nothing raises; the campaign simply stops, and the asset takes months to replace |
| Worker ticking | No tick in five minutes, or never | The outbox signal cannot fail while nothing is due. A worker that died on a quiet weekend — or a cron that never fired — looked healthy until the first action was due, and thirty minutes more (D-39). Every tick writes a heartbeat; a fresh deployment is not draining until something has ticked |

The endpoint is unauthenticated and returns counts only, never a tenant's identifiers: it answers an operator's question, and returning identifiers would answer a different one.

### Keys

A key that leaks — pasted into a chat, committed to the partner's repository — has to stop working without a shell.

| | |
|---|---|
| `GET /v1/keys` | Prefixes, scopes and `last_used_at`. Never a token; a token is shown once, at creation |
| `POST /v1/keys` | Issue another |
| `POST /v1/keys/{id}/rotate` | Issue a replacement, **then** revoke the original. Revoking first leaves a window with no working key, and a rotation that causes an outage is one nobody performs a second time |
| `DELETE /v1/keys/{id}` | Revoke. Idempotent, and never deletes the row: `last_used_at` still answers "was this key used after it leaked", which is the first question anybody asks |

A key cannot revoke itself — that locks the tenant out of their own account — and the error says to rotate instead.

### Rate limiting

`POST /v1/signup` is the only route that takes a write without a key. Its tokens are 32 random bytes and single-use, so guessing one is not the threat; volume is. A sliding window caps it at 20 requests per minute per caller.

**The limiter is per process.** Two machines allow twice the traffic and a restart forgets everything. That is stated rather than hidden: at this scale a per-process ceiling is most of the value for none of the operational cost of shared state, and a limiter that needs Redis to exist is a limiter nobody turns on. When a second machine matters it is replaced by a counter in Postgres, not extended.

### Restoring

Neon takes the backups. The part that goes wrong is the restore, and it goes wrong quietly.

`pg_dump` of one database emits `GRANT … TO zolts_app` and no `CREATE ROLE`, because roles are cluster-wide. Restored into a fresh project the role does not exist, every `GRANT` fails — and `psql` without `ON_ERROR_STOP=1` exits 0 anyway. The restore reports success, the API starts, connects, and cannot read a single row.

```sh
scripts/restore.sh backup.sql "$OWNER_URL" "$APP_PASSWORD"
```

Four steps: create the role if absent, restore with `ON_ERROR_STOP=1`, re-grant via `migrate`, then `preflight` the result. A restore that has not been preflighted is a backup nobody has tested.

## Changing a program without a deploy

The console edits eleven parameters and emits a whole program document. Nothing else.

| What it edits | Bound | Where the bound comes from |
|---|---|---|
| `experiment.holdout_pct` | 0–50 | the schema |
| `score.floor` | 0–100 | the schema |
| `trigger.window`, `trigger.dedupe.cooldown` | `^\d+[hdw]$`, `^\d+[dw]$` | the schema |
| `route.strategy`, `budget.on_exceed` | three choices each | the schema |
| `budget.monthly_credits` | ≥ 0 | the schema |
| `policy.overrides.max_touches_per_person_per_week` | ≥ 0 | the schema |
| `enrich.account.max_cost_per_account`, `enrich.person.max_cost_per_contact` | ≥ 0 | the schema |
| `enrich.person.accuracy_sla` | 0–1 | the schema |

`zolts.dsl.controls()` reads those bounds at call time. Nothing restates them, so the form cannot offer a value `POST /v1/programs` rejects.

**It emits a document, never a patch.** The form starts from the program's own spec, applies the changed dials, bumps the patch version, and posts the whole thing — which is then validated against the schema, linted, and holdout-checked exactly as `scripts/validate.py` does offline. A patch would make the runtime decide how to merge, and a program with two authors is the divergence ADR-002 exists to prevent.

**Publishing writes a new version.** v2.1.0 keeps running and v2.1.1 arrives as a draft. Editing in place would leave enrollments already executing under the old spec pointing at one that no longer describes what they did.

**An emptied box is refused.** A field that held `0.35` and is now blank is ambiguous between "keep it" and "remove the ceiling", and the second uncaps what the program may spend per account. The editor names the field and disables publish.

What it does not edit: the audience SQL, the plays, the trigger's events. Those decide who is contacted and what is said, and they belong in a diff somebody reviews (decision 30).

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

1038 tests. 340 of them run against a real Postgres (`pytest -m db`) and are skipped, never faked, when one is absent — an isolation property verified against a stub is not verified. CI fails a run that skipped them.

Both figures were wrong until a test measured them. README put the second figure at 302; the real one was barely over half that. Nobody wrote it dishonestly — a `skipif` cannot be selected for, so the number was never re-measurable and so was never re-measured. Collection is now marked by fixture closure, which counts a test that requests the `db` fixture as well as one carrying the decorator, and a test asserts both figures against the documents.

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
22. Every path the runtime reads is shipped in the image, and a missing directory raises rather than reading as an empty catalogue.
23. Preflight blocks a production release on a published secret key, an application role that can bypass row-level security, a pending migration, or a query with no tenant that returns rows.
24. An invitation is redeemed once, including by two requests racing, and invalid, expired and redeemed answer identically.
25. The role that serves tenant requests cannot read the invitations table.
26. A revoked key stops working, a key cannot revoke itself, and rotation issues the replacement before revoking the original.
27. A stalled outbox is reported while `/health` still says ok.
28. The signup route refuses a caller past its ceiling, and the window slides.
29. A dump grants to a role it does not create, which is why the restore script creates it first.
30. A reply verdict whose quote is not in the reply is discarded, and an unsubscribe written in prose suppresses.
31. A webhook still lands a reply when the classifier is unavailable, raises, or is switched off.
32. A browser typing `/console` gets a sign-in page, and the page's CSP hash matches the script actually served.
33. A cookie-authenticated write without the CSRF token is refused; the same write with `x-api-key` is not.
34. Revoking a key ends the sessions it opened, and a session cannot carry scopes its key lacked.
35. A draft appears in the console's program list, carries an id, and a real browser can click it live.
36. The review queue carries what every gate said, agrees with the rail's count, and is tenant-scoped.
37. A real browser opens the queue, approves a draft, and the proposal is decided in Postgres.
38. An unread reply counts and is marked unread; a read one records who read it; the measurement reports the share and the console renders it beside the lift.
39. Every price in `docs/12` matches `zolts/billing.py`, for every action and every plan.
40. An unpriced action raises rather than costing nothing, and enterprise has no default terms.
41. A tenant at its ceiling holds its work instead of losing it, and a closed period keeps the terms it was sold.
42. Salesforce follows the page URL the server returns, terminates on a repeated one, and refuses to read without an instance_url.
43. A boolean opt-out reports OPTED_OUT for true and legitimate interest for false, and never claims to know 'never asked'.
44. A step whose condition excludes this contact is skipped, and a sequence whose remaining steps are all excluded finishes in one write.
45. A send lands inside the declared window, is never brought forward, and follows the customer's local time across daylight saving.
