-- Zolts core entities.
--
-- Every tenant-scoped table enables AND forces row-level security. Forcing
-- matters: without it the table owner bypasses the policy, so a migration run
-- and an application query would see different data. The application connects
-- as a non-owning role that cannot bypass RLS under any circumstance.
--
-- The tenant is established per transaction with `set local zolts.tenant_id`.
-- A missing setting resolves to the empty string, which fails the uuid cast,
-- so an unscoped query raises rather than returning another tenant's rows.

create extension if not exists citext;

create table tenant (
  id              uuid primary key default gen_random_uuid(),
  slug            text not null unique,
  name            text not null,
  region          text not null check (region in ('eu','us','apac')),
  blueprint_id    text not null,
  compliance_tier text not null default 'standard',
  status          text not null default 'active' check (status in ('active','suspended')),
  created_at      timestamptz not null default now()
);

create table account (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references tenant(id) on delete cascade,
  domain        text,
  legal_id      text,
  name          text not null,
  country       text,
  employee_band text,
  industry_code text,
  crm_id        text,
  attributes    jsonb not null default '{}',
  confidence    numeric(4,3) not null default 1.0,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create unique index account_tenant_domain_uq on account (tenant_id, domain) where domain is not null;
create unique index account_tenant_crm_uq on account (tenant_id, crm_id) where crm_id is not null;
create index account_tenant_country_ix on account (tenant_id, country);

create table person (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references tenant(id) on delete cascade,
  email         citext,
  email_status  text check (email_status in ('verified','risky','invalid','unknown')),
  linkedin_urn  text,
  full_name     text,
  country       text,
  consent_state jsonb not null default '{}',
  attributes    jsonb not null default '{}',
  crm_id        text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create unique index person_tenant_email_uq on person (tenant_id, email) where email is not null;
create unique index person_tenant_crm_uq on person (tenant_id, crm_id) where crm_id is not null;

create table membership (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references tenant(id) on delete cascade,
  person_id   uuid not null references person(id) on delete cascade,
  account_id  uuid not null references account(id) on delete cascade,
  title       text,
  seniority   text,
  department  text,
  buying_role text,
  started_at  date,
  ended_at    date,
  unique (tenant_id, person_id, account_id, started_at)
);
create index membership_account_ix on membership (tenant_id, account_id);

create table signal (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references tenant(id) on delete cascade,
  entity_type  text not null check (entity_type in ('account','person')),
  entity_id    uuid not null,
  type         text not null,
  strength     numeric(4,3) not null check (strength >= 0 and strength <= 1),
  half_life_h  int not null check (half_life_h > 0),
  source       text not null,
  legal_basis  text not null,
  payload      jsonb not null default '{}',
  dedupe_key   text,
  observed_at  timestamptz not null,
  ingested_at  timestamptz not null default now()
);
create index signal_entity_ix on signal (tenant_id, entity_id, observed_at desc);
create index signal_type_ix on signal (tenant_id, type, observed_at desc);
-- A source that replays its feed must not inflate intent. Ingest is idempotent
-- on this key, so replay is a no-op rather than a second observation.
create unique index signal_dedupe_uq on signal (tenant_id, dedupe_key) where dedupe_key is not null;

create table program (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references tenant(id) on delete cascade,
  key        text not null,
  version    text not null,
  spec       jsonb not null,
  spec_hash  text not null,
  status     text not null check (status in ('draft','staged','live','paused','archived')),
  created_by text,
  created_at timestamptz not null default now(),
  unique (tenant_id, key, version)
);
-- At most one live version per program key. Enforced by the database, not by
-- the application: two concurrent publishes would otherwise both go live and
-- the holdout assignment would split across two specs.
create unique index program_one_live_uq on program (tenant_id, key) where status = 'live';

create table enrollment (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references tenant(id) on delete cascade,
  program_id  uuid not null references program(id) on delete cascade,
  entity_type text not null check (entity_type in ('account','person')),
  entity_id   uuid not null,
  variant     text not null check (variant in ('treatment','control')),
  score       numeric(6,3),
  tier        text,
  state       text not null,
  step_index  int not null default 0,
  next_run_at timestamptz,
  context     jsonb not null default '{}',
  entered_at  timestamptz not null default now(),
  exited_at   timestamptz,
  exit_reason text,
  unique (tenant_id, program_id, entity_type, entity_id)
);
create index enrollment_due_ix on enrollment (tenant_id, next_run_at) where exited_at is null;

create table touch (
  id              uuid primary key default gen_random_uuid(),
  tenant_id       uuid not null references tenant(id) on delete cascade,
  enrollment_id   uuid references enrollment(id) on delete cascade,
  channel         text not null,
  direction       text not null default 'out' check (direction in ('out','in')),
  step_key        text,
  idempotency_key text not null,
  content         jsonb not null default '{}',
  provider        text,
  provider_ref    text,
  status          text not null check (status in ('queued','sent','delivered','bounced','opened','replied','failed')),
  cost_micros     bigint not null default 0,
  sent_at         timestamptz,
  created_at      timestamptz not null default now(),
  unique (tenant_id, idempotency_key)
);
create index touch_enrollment_ix on touch (tenant_id, enrollment_id, sent_at desc);

create table outcome (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references tenant(id) on delete cascade,
  enrollment_id uuid references enrollment(id) on delete cascade,
  account_id    uuid references account(id) on delete cascade,
  type          text not null,
  value_micros  bigint,
  occurred_at   timestamptz not null,
  source        text not null,
  dedupe_key    text,
  created_at    timestamptz not null default now()
);
create unique index outcome_dedupe_uq on outcome (tenant_id, dedupe_key) where dedupe_key is not null;
create index outcome_enrollment_ix on outcome (tenant_id, enrollment_id, occurred_at desc);

create table policy_decision (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references tenant(id) on delete cascade,
  subject_type text not null,
  subject_id   uuid not null,
  action       text not null,
  decision     text not null check (decision in ('allow','deny','review')),
  rule_key     text not null,
  jurisdiction text,
  rationale    text,
  decided_at   timestamptz not null default now()
);
create index policy_decision_subject_ix on policy_decision (tenant_id, subject_id, decided_at desc);

create table cost_event (
  id             uuid primary key default gen_random_uuid(),
  tenant_id      uuid not null references tenant(id) on delete cascade,
  program_id     uuid references program(id) on delete set null,
  kind           text not null,
  provider       text,
  units          numeric not null,
  cost_micros    bigint not null,
  billed_credits numeric not null default 0,
  occurred_at    timestamptz not null default now()
);
create index cost_event_ix on cost_event (tenant_id, program_id, occurred_at);

create table suppression (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references tenant(id) on delete cascade,
  scope      text not null check (scope in ('email','domain','person','account')),
  value      citext not null,
  reason     text not null,
  source     text not null,
  created_at timestamptz not null default now(),
  unique (tenant_id, scope, value)
);
