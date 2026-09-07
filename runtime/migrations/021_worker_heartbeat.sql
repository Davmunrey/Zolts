-- Where the workers say they are alive.
--
-- `outbox draining` fails when work is due and nothing has claimed it for
-- thirty minutes. While nothing is due it cannot fail, so a worker that died
-- on a quiet Friday is reported healthy until Monday's first action is due,
-- and half an hour after that (D-39). On a host with no worker process —
-- Vercel, where a cron invokes one tick a minute — "is anything ticking" has
-- no process to ask, and this table is the answer.
--
-- Not tenant-scoped: a tick spans every tenant and the question is an
-- operator's. One row per worker name. A serverless instance is a new name
-- on every cold start, so the writer prunes rows older than a week.
create table worker_heartbeat (
  name       text primary key,
  ticked_at  timestamptz not null default now(),
  detail     jsonb not null default '{}'::jsonb
);
