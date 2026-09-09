-- What a tenant's go-to-market cost during one billing period, declared.
--
-- The baseline (022) captures the same four figures once, at onboarding, and
-- the incrementality report prorates that monthly run-rate to price a meeting.
-- Nothing re-measures it, so a tenant who hires two more people keeps being
-- priced at the figure they gave on their first day, and the error is
-- invisible because the report has no other number to disagree with
-- (decision 46).
--
-- One row per period, written once, for the same reason the baseline is
-- (ADR-042): a figure a signed report divides by must not be one somebody
-- chose after reading the result. Declaring is optional — a period with no row
-- falls back to the run-rate and the report says which basis it used — because
-- making a month's close depend on a data-entry step would block billing.
create table tenant_period_spend (
  -- The period is the key: one declaration each, and it disappears with the
  -- period it describes.
  period_id             uuid primary key references billing_period(id) on delete cascade,
  tenant_id             uuid not null references tenant(id) on delete cascade,
  -- EUR per period, in micros. Not per month and never prorated: the figures
  -- are for this period, which is the assumption the run-rate carried.
  spend_tools_micros    bigint not null check (spend_tools_micros >= 0),
  spend_data_micros     bigint not null check (spend_data_micros >= 0),
  spend_sending_micros  bigint not null check (spend_sending_micros >= 0),
  spend_people_micros   bigint not null check (spend_people_micros >= 0),
  -- Derived once and stored, like the baseline's own figures, so the number a
  -- report divided by never moves with a formula.
  total_micros          bigint not null check (total_micros >= 0),
  digest                text   not null,
  declared_by           text   not null,
  declared_at           timestamptz not null default now()
);

create index tenant_period_spend_tenant_ix on tenant_period_spend (tenant_id);

alter table tenant_period_spend enable row level security;
alter table tenant_period_spend force row level security;
create policy tenant_period_spend_tenant_isolation on tenant_period_spend
  using (tenant_id = zolts_internal.current_tenant())
  with check (tenant_id = zolts_internal.current_tenant());
