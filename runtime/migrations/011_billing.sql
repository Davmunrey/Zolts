-- Turning usage into something you can charge for.
--
-- `cost_event.billed_credits` has existed since the first migration and no
-- caller ever set it. The price list in docs/12 is complete and the runtime
-- billed nothing: the column was there, the structure was there, and every
-- action was free.
--
-- A tenant has a plan and a period. The period is the unit that closes, and
-- closing it is what produces a statement somebody can be sent.
alter table tenant add column plan text not null default 'starter'
  check (plan in ('starter', 'growth', 'scale', 'enterprise'));

-- Negotiated terms, for the one plan whose numbers are not in the price list.
-- Null on every standard plan: a default here would be a number somebody
-- eventually invoices.
alter table tenant add column negotiated_terms jsonb;

alter table tenant add constraint tenant_enterprise_has_terms check (
  plan <> 'enterprise' or negotiated_terms is not null);

create table billing_period (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid        not null references tenant(id) on delete cascade,
  starts_at     timestamptz not null,
  ends_at       timestamptz not null,
  plan          text        not null,
  -- Copied from the tenant when the period opens, not read at close time. A
  -- customer who upgrades mid-month is billed on what they were sold for the
  -- period they consumed, and a price list that changes must not silently
  -- restate a closed month.
  included_credits numeric   not null,
  platform_eur     numeric   not null,
  seats_included   int       not null,
  consumed_credits numeric   not null default 0,
  seats_used       int       not null default 0,
  closed_at     timestamptz,
  statement     jsonb,
  created_at    timestamptz not null default now(),

  constraint billing_period_span check (ends_at > starts_at),
  -- A closed period has a statement and a statement means closed. Anything
  -- else is a month nobody can reconcile.
  constraint billing_period_closed_together check (
    (closed_at is null and statement is null)
    or (closed_at is not null and statement is not null))
);

-- One open period per tenant. Two would double-count every credit.
create unique index billing_period_one_open on billing_period (tenant_id)
  where closed_at is null;
create index billing_period_tenant on billing_period (tenant_id, starts_at desc);

alter table billing_period enable row level security;
alter table billing_period force row level security;
create policy billing_period_tenant_isolation on billing_period
  using (tenant_id = zolts_internal.current_tenant())
  with check (tenant_id = zolts_internal.current_tenant());

-- Which period a cost event fell in. Set at write time rather than derived
-- from a timestamp at read time: a period that closes while an action is in
-- flight must not move the credit into the next month.
alter table cost_event add column billing_period_id uuid
  references billing_period(id) on delete set null;
create index cost_event_period on cost_event (billing_period_id)
  where billing_period_id is not null;
