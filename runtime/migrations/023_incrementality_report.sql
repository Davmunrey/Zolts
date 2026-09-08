-- What one program did against its holdout, frozen at the close of a billing
-- period.
--
-- `docs/10` says every program reports a real income statement and `docs/17`
-- says the month-9 conversation compares after with before. The "before" is
-- frozen (ADR-042, `tenant_baseline`); nothing froze the "after". The
-- measurement endpoint and the console recomputed the lift on every request,
-- so the figure a partner read in month three was not the figure anybody
-- could show in month nine (D-46).
--
-- One row per program per closed billing period, written once, and the
-- rendered document stored verbatim beside its canonical fields: a change to
-- the template or to a formula never restates a report somebody signed
-- (ADR-043). The verdict is one of three words, and "met" is not one of them.
create table incrementality_report (
  id                 uuid primary key default gen_random_uuid(),
  tenant_id          uuid not null references tenant(id) on delete cascade,
  program_id         uuid not null references program(id) on delete cascade,
  billing_period_id  uuid not null references billing_period(id) on delete cascade,
  -- Cumulative: from the program's first enrollment to the period's end.
  period_start       date not null,
  period_end         date not null,
  verdict            text not null
                     check (verdict in ('significant', 'not significant', 'not resolvable')),
  -- Every field the digest covers, inputs and derived alike (zolts.report).
  body               jsonb not null,
  digest             text not null,
  rendered           text not null,
  frozen_by          text not null,
  frozen_at          timestamptz not null default now(),
  unique (tenant_id, program_id, billing_period_id),
  check (period_end > period_start)
);

alter table incrementality_report enable row level security;
alter table incrementality_report force row level security;
create policy incrementality_report_tenant_isolation on incrementality_report
  using (tenant_id = zolts_internal.current_tenant())
  with check (tenant_id = zolts_internal.current_tenant());
