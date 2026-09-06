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
| HubSpot CRM, in and out | `runtime/connectors/hubspot.py` | Written, not yet run against a live portal |
| Smartlead sending | `runtime/connectors/smartlead.py` | Written, not yet run against a live account |
| HTTP API with API-key tenancy | `runtime/api/` | Running |
| CLI: migrate, provision, worker, serve | `runtime/cli.py` | Running |

Not built: the agent layer and its eval gate, the enrichment waterfall wired to real providers, warehouse zero-copy, the console reading live data rather than a build-time fixture, and any second sending channel. Those are named here so the gap is a decision rather than a discovery.

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

`ZOLTS_SECRET_KEY` seals connector credentials with AES-GCM before they reach the database, so a dump discloses nothing on its own. Losing it means re-entering every credential; it belongs in a secret manager, not in the database it protects.

## What would break first at scale

| Limit | Bites at roughly | Fix when it does |
|---|---|---|
| Single claim query across all tenants | Low thousands of actions per second | Partition `action` by tenant hash, one claim per partition |
| One worker process per deployment | Same | The lease already makes workers horizontally safe; run more |
| `plan_due` scans every active tenant per tick | Thousands of tenants | Drive planning from a due-time index rather than a tenant loop |
| Contact-local hour approximated by country | Immediately, for accuracy | Resolve a real timezone per contact; the coarse table is marked in the code |
| Provider rate limits are the connector's problem | First large tenant | A shared token bucket per (tenant, provider) in the claim path |

None is load-bearing before the first paying customers, and each is a contained change. They are listed so that the first one to bite is a known cost rather than an outage.

## Tests

332 tests. The runtime's 62 run against a real Postgres and are skipped, never faked, when one is absent — an isolation property verified against a stub is not verified. CI fails a run that skipped them.

What they assert, in the order that matters:

1. A tenant reads only its own rows; writing another tenant's id is rejected; a query with no tenant raises.
2. A program with no declared holdout refuses to enroll, and a zero holdout needs a written justification.
3. A control enrollment is never scheduled and the planner refuses it again.
4. Every dispatched action carries a policy decision id.
5. A denied action is cancelled, not retried.
6. A redelivered action does not send twice.
7. An exited enrollment's queued work is cancelled.
8. A tenant with no connection fails loudly rather than silently.
