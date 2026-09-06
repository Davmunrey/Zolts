-- Going and looking, instead of waiting to be told.
--
-- `docs/06` is a catalogue of thirty-odd signals with half-lives, tiers and
-- action SLAs, and nothing watched it. A signal reached this runtime only when
-- the customer pushed one at `POST /v1/signals` or a CRM sync implied one — so
-- the product learned about a funding round when its customer already knew.
--
-- `docs/06` opens by calling signal-to-action latency the highest-leverage
-- variable in the whole GTM system, and a product SLA rather than an
-- implementation detail. A runtime that is told cannot have a latency; it can
-- only inherit somebody else's.

-- What was looked for, when, and whether anything was there.
--
-- Three jobs in one table, which is one table too few to be tempting and
-- exactly enough to keep the answers consistent:
--
--   * the refresh clock. When this account was last examined for this signal.
--   * the bill. `docs/12` prices a check per account per day, so the first
--     row for an account on a UTC day is the one that costs 0.5 credits and
--     every later row that day is free. A definition with a six-hour refresh
--     therefore costs the same as one with a daily refresh, which is what
--     makes a fast refresh affordable at all.
--   * the audit. A customer asking why a play never fired deserves "we looked
--     forty times and found nothing" rather than a shrug.
create table signal_check (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references tenant(id) on delete cascade,
  signal_key   text not null,
  entity_type  text not null check (entity_type in ('account','person')),
  entity_id    uuid not null,
  -- Detected, and then separately whether it was fresh enough to act on. A
  -- source that finds a three-day-old event on a signal with a four-hour SLA
  -- did its job; the runtime declining to act on it is a different fact and
  -- hiding it would make a working source look broken.
  detected     boolean not null default false,
  stale        boolean not null default false,
  checked_at   timestamptz not null default now(),
  billed       boolean not null default false
);
create index signal_check_clock on signal_check
  (tenant_id, signal_key, entity_id, checked_at desc);
-- The billing question is "has this account been checked at all today", so the
-- index is on the day rather than on the signal.
create index signal_check_day on signal_check
  (tenant_id, entity_id, ((checked_at at time zone 'UTC')::date));

alter table signal_check enable row level security;
alter table signal_check force row level security;
create policy signal_check_tenant_isolation on signal_check
  using (tenant_id = zolts_internal.current_tenant())
  with check (tenant_id = zolts_internal.current_tenant());
