# 25 · Runbook

What an operator does when something breaks, at three in the morning, without reading thirty-nine ADRs first.

**Every command on this page was executed while it was written.** Where output is shown, it is the output that came back — not an example of what one might look like. The one thing this page cannot claim is production experience: there is no production (`docs/20`), so these are the procedures a first incident will correct.

## Start here

```bash
python3 -m runtime.cli liveness      # exits non-zero when a signal is failing
curl -fsS https://<host>/health/liveness   # 503 when a signal is failing
```

```
OK    outbox draining: nothing due
OK    actions completing: 0 dead in the last 24 hours
OK    connections healthy: no connection is in error
OK    tenants can send: every tenant with live programs can send
OK    sending domains: no domain is paused by a cut-off

DRAINING
```

Six signals, and the section below with the same name says what to do about each. `/health` answers whether the process is up; this answers whether it is doing anything. They are different questions and the second is the one a customer notices.

---

## `outbox draining` is failing

```
FAIL  outbox draining: 1 actions due, the oldest 90 minutes ago. Nothing has
      claimed them, which means no worker is running or every worker is
      failing before it claims
```

**What it means.** Work is queued and nothing is taking it. The API is up — this signal is served by the API — so the customer's programs look alive and are doing nothing.

**Diagnose.**

```sql
select kind, channel, attempts, date_trunc('second', now() - run_after) as overdue,
       left(coalesce(last_error,'—'), 60) as last_error
  from action where state = 'pending' and run_after < now()
 order by run_after limit 20;
```

```
   kind   | channel | attempts | overdue  | last_error
----------+---------+----------+----------+------------
 dispatch | email   |        1 | 01:30:01 | —
```

| What you see | What it is |
|---|---|
| `attempts = 0`, no error | nothing has ever picked it up: **the worker is not running** |
| `attempts > 0`, an error repeated across rows | the worker runs and fails on the same thing every time |
| rows with `leased_until` in the future | a worker claimed them and died; they free themselves when the lease expires |

**Fix.**

```bash
# Fly: the worker is a process.
fly status --app zolts                    # is the worker machine up?
fly logs --app zolts --instance <worker>  # what did it say before it stopped?
# Vercel: the worker is the cron. Is it firing, and what did it answer?
npx vercel crons ls                       # the schedules the production deployment declares
npx vercel logs https://zolts.vercel.app  # what /api/tick said on its last invocations
# Either host, from anywhere holding the database URLs:
python3 -m runtime.cli worker --once      # drain one tick by hand, and watch it
```

`worker ticking` fails first on either host — see the section below — and its detail says whether nothing has ever ticked or something stopped.

One tick prints exactly what it did:

```json
{"planned": 1, "claimed": 1, "succeeded": 0, "cancelled": 1, "deferred": 0,
 "failed": 0, "dead": 0, "errors": ["tenant has no active connection for channel 'email'"]}
```

`cancelled` with that error is the second case below, not a queue problem.

**Do not** delete the pending rows. Every action carries an idempotency key (product invariant 2); re-running is safe and deleting loses the audit trail of what was owed.

---

## `worker ticking` is failing

```
FAIL  worker ticking: no worker has ever ticked. Nothing is draining the
      outbox: the worker process is not running, or the cron that invokes
      /api/tick has not fired
```

**What it means.** Nothing is running the outbox, and it does not matter whether there is work in it. Every tick writes a heartbeat; this fails when the last one is older than five minutes or there has never been one. It is the signal that catches a worker that died on a quiet weekend, which `outbox draining` cannot see until the first action is due (D-39).

**Diagnose.**

```sql
select name, ticked_at, date_trunc('second', now() - ticked_at) as ago, detail
  from worker_heartbeat order by ticked_at desc limit 5;
```

| What you see | What it is |
|---|---|
| no rows | nothing has ever ticked: the worker was never started, or the cron never fired. On Vercel, a deployment on the Hobby plan has no minute cron |
| rows, all old | it ran and stopped: a crashed worker process, a cron the platform disabled, or a `CRON_SECRET` that changed so every invocation is refused with 401 |
| a fresh row every minute with `claimed: 0` | the worker is fine; the outbox is empty. If `outbox draining` fails at the same time, the tick is failing before it claims — read its `errors` in the function logs |

**Fix.**

```bash
# Fly
fly status --app zolts
# Vercel: is the cron declared on the production deployment, and did it answer 200?
npx vercel crons ls
npx vercel logs https://zolts.vercel.app
# One tick by hand, with the cron's secret from the environment — never typed
# into a command line, which is a shell history and a process list.
read -rs CRON_SECRET && export CRON_SECRET
curl -fsS -H "Authorization: Bearer $CRON_SECRET" https://zolts.vercel.app/api/tick
```

A 401 from that `curl` is a secret that does not match the one the deployment holds; a 503 is a deployment with no secret at all. Either way the cron has been refused on every minute since, and the fix is the variable, not the code.

---

## `actions completing` is failing

```
FAIL  actions completing: N actions exhausted their attempts in the last 24 hours
```

**What it means.** Actions are being retried until they die. One is a bad address. A wall of them is an integration that stopped working — an expired token, a provider that changed a field, a suspended account.

**Diagnose.**

```sql
select channel, left(last_error, 80) as error, count(*)
  from action where state = 'dead' and updated_at > now() - interval '24 hours'
 group by 1, 2 order by 3 desc;
```

The errors group by cause. One line with a high count is one broken integration.

| Error contains | What to do |
|---|---|
| `401`, `403`, `invalid_grant` | the credential expired: re-enter it (`runtime.cli connect`), then requeue |
| `429` | the provider is rate-limiting; see **A provider is 429ing** below |
| `no active connection for channel` | the tenant has no connector for that step's channel |
| a provider's own validation message | the data is wrong, not the runtime: fix the record and let the next enrolment carry it |

**Requeue after fixing the cause**, never before:

```sql
update action set state = 'pending', attempts = 0, run_after = now(), last_error = null
 where state = 'dead' and updated_at > now() - interval '24 hours' and channel = 'email';
```

---

## `connections healthy` is failing

**What it means.** A stored connection is in `status = 'error'`. The runtime marked it after a permanent failure, and every program that needs that channel is stopped for that tenant.

**Diagnose and fix.**

```sql
select tenant_id, provider, status, left(last_error, 80) from connection where status = 'error';
```

Re-enter the credential. It is sealed on write, so there is no way to inspect the old one and no way to recover it — that is the design (`ADR-039`).

```bash
# The token is read from stdin, never from an argument: a credential in a
# command line is a credential in the shell history and in `ps`.
printf '%s' "$TOKEN" | python3 -m runtime.cli connect --tenant <id> \
    --provider hubspot --secret-stdin
```

---

## `tenants can send` is failing

**What it means.** A tenant has a live program whose plays name a channel it has no connector for. The program enrols people and every step cancels.

```json
{"cancelled": 1, "errors": ["tenant has no active connection for channel 'email'"]}
```

**Fix.** Either connect the channel, or pause the program until it is connected. A program that enrols and cancels burns the cooldown: those accounts will not be re-enrolled for the dedupe window (180 days on the flagship), so the cost of leaving it running is a segment you cannot touch again this year.

---

## `sending domains` is failing

**What it means.** A circuit breaker paused a domain because its complaint or bounce rate crossed the cut-off (`ADR-020`). This is the most expensive silent state in the system: the domain's reputation is already damaged, and sending more finishes it.

**Diagnose.**

```bash
python3 -m runtime.cli capacity --tenant <id>
```

```json
{"managed": false, "capacity": 0, "remaining": 0, "domains": [],
 "note": "No sending domain is registered. Capacity is whatever the sending provider allows, and this runtime cannot stop a send or pause a burning domain."}
```

That note is its own incident: **a tenant with no registered domain has no cap this runtime can enforce.** Register the domain rather than raising a cap.

**Fix a burned domain.** Do not un-pause it and resume. Warm a second domain, move the tenant's sending to it, and leave the burned one silent for weeks. A paused domain that starts sending again at its old volume is a domain that gets blocklisted rather than throttled.

---

## A provider is 429ing

**What it means.** Rate limits, not failures. The runtime defers rather than retries hard, so a 429 storm shows up as a growing queue rather than dead actions.

**Diagnose.** `liveness` shows the outbox growing while `actions completing` stays green.

**Fix.** Lower the tenant's weekly capacity in the program (`route.tiers[].capacity_per_week`) — the console can change that without a pull request (`ADR-029`) — or raise the limit with the provider. Do not raise `max_attempts`: retrying a rate limit faster is how a soft limit becomes a hard one.

---

## A credential cannot be opened

```
FAIL  sealed credentials: 3 of 41 credentials are sealed under a key this
      deployment does not hold
```

**What it means.** `ZOLTS_SECRET_KEY` changed without a rotation, or a rotation was left half-finished and the old key was removed. Preflight refuses to start in production for this reason, so it is usually seen at deploy time rather than at three in the morning.

**Fix.**

```bash
python3 -m runtime.cli rotate-key --check     # how many, and under which key
fly secrets set ZOLTS_PREVIOUS_SECRET_KEYS="<the old key>"
python3 -m runtime.cli rotate-key             # resumable; re-run if interrupted
fly secrets unset ZOLTS_PREVIOUS_SECRET_KEYS  # this step is the rotation
```

On Vercel the two `fly secrets` lines are `npx vercel env add ZOLTS_PREVIOUS_SECRET_KEYS production` (it prompts for the value; never put it in the command line) and `npx vercel env rm ZOLTS_PREVIOUS_SECRET_KEYS production`, each followed by a release so the function picks it up.

If the old key is genuinely gone, nothing in the database can recover those credentials. The CLI says so rather than implying a retry would help, and the fix is to re-enter them.

---

## A program stopped enrolling and nothing is failing

**What it means.** The audience refuses. The most common cause is the open-deal exclusion with no CRM that reads deals (`ADR-038`): the program is correct, the clause is correct, and the runtime refuses rather than contacting accounts it cannot rule out.

**Diagnose.**

```sql
select detail->>'program' as program, detail->>'reason' as reason, count(*)
  from audit_log where action = 'enrollment.refused'
   and at > now() - interval '24 hours' group by 1, 2 order by 3 desc;
```

The console's **Audit log** view answers the same question without SQL.

**Fix.** Connect a CRM that reads deals and sync it, or remove the clause from the program and accept the exposure. Both are decisions, not repairs.

---

## A source's signals stopped matching

**What it means.** The signals arrive, the programs are live, and nothing enrols. The most common cause after a customer changes anything upstream: a payload field that is now the wrong type. The runtime treats that as a non-match rather than an error (`ADR-040`), so nothing is failing anywhere.

**Diagnose.**

```sql
select detail->>'program' as program, detail->>'signal' as signal,
       detail->'clauses'->0->>'where' as clause,
       detail->'clauses'->0->>'error' as error, count(*)
  from audit_log where action = 'signal.unanswerable'
   and at > now() - interval '24 hours' group by 1, 2, 3, 4 order by 5 desc;
```

**Fix.** It is the sender's to fix, and they were already told: the same text is returned in `POST /v1/signals` under `warnings`. Send them the clause and the error. If their field genuinely changed shape, the program's `where` is what changes here, not the runtime.

---

## Restoring from a backup

`scripts/restore.sh`, executed end to end in CI against a real dump (`ADR-033`). Read it before running it: step 1 creates the application role, and a restore into a cluster where that role already exists behaves differently from one where it does not — which is the failure the test reproduces (D-26).

---

## What this page does not cover

| Not here | Why |
|---|---|
| Paging and on-call rotation | one operator, no rotation to describe |
| Provider-side incidents | the provider's status page is the source of truth, not this repo |
| Anything measured in production | there is none yet; the first incident corrects this page |
