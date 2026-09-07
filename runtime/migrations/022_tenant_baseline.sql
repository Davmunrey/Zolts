-- What a tenant's GTM cost and produced before Zolts, frozen at onboarding.
--
-- `docs/17` calls capturing this the irreversible phase-1 requirement: it
-- cannot be reconstructed later, and without it the month-9 expansion has no
-- "before" to compare against. `docs/13` makes it an exit criterion and
-- `docs/14` a KPI. Nothing stored one (D-41).
--
-- One row per tenant and written once. The derived figures are stored rather
-- than computed on read, so the document a partner signed never changes with
-- a formula; the digest is over the canonical fields, so the letter and the
-- row can be checked against each other by anyone holding both (ADR-042).
create table tenant_baseline (
  tenant_id                    uuid primary key references tenant(id) on delete cascade,
  window_start                 date not null,
  window_end                   date not null,
  -- Declared at onboarding: four fields, EUR per month, in micros.
  spend_tools_micros           bigint not null,
  spend_data_micros            bigint not null,
  spend_sending_micros         bigint not null,
  spend_people_micros          bigint not null,
  -- The window's funnel, declared or read from the CRM.
  contacted                    int not null,
  replied                      int not null,
  meetings                     int not null,
  opportunities                int not null,
  -- Derived once. Null, not zero, when there was nothing to divide by.
  cost_per_meeting_micros      bigint,
  cost_per_opportunity_micros  bigint,
  source                       text not null check (source in ('declared', 'crm')),
  digest                       text not null,
  signed_by                    text not null,
  captured_by                  text not null,
  frozen_at                    timestamptz not null default now(),
  check (window_end > window_start),
  check (contacted >= replied and contacted >= meetings)
);

alter table tenant_baseline enable row level security;
alter table tenant_baseline force row level security;
create policy tenant_baseline_tenant_isolation on tenant_baseline
  using (tenant_id = zolts_internal.current_tenant())
  with check (tenant_id = zolts_internal.current_tenant());
