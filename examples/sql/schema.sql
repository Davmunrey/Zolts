-- Zolts · núcleo canónico (extracto de referencia, Postgres 16)
-- Todas las tablas con RLS por tenant_id.

create extension if not exists "uuid-ossp";
create extension if not exists vector;

create table tenant (
  id              uuid primary key default uuid_generate_v4(),
  name            text not null,
  region          text not null check (region in ('eu','us','apac')),
  blueprint_id    text not null,                 -- arquetipo resuelto
  compliance_tier text not null default 'standard',
  created_at      timestamptz not null default now()
);

create table account (
  id            uuid primary key default uuid_generate_v4(),
  tenant_id     uuid not null references tenant(id),
  domain        text,
  legal_id      text,                            -- VAT/CIF/EIN
  name          text not null,
  country       text,                            -- ISO-3166, dirige el policy engine
  employee_band text,
  industry_code text,                            -- NACE/SIC/NAICS normalizado
  crm_id        text,
  attributes    jsonb not null default '{}',     -- validado contra tenant_schema
  confidence    numeric(4,3) not null default 1.0,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create unique index on account (tenant_id, domain) where domain is not null;
create index on account (tenant_id, country);

create table person (
  id             uuid primary key default uuid_generate_v4(),
  tenant_id      uuid not null references tenant(id),
  email          citext,
  email_status   text check (email_status in ('verified','risky','invalid','unknown')),
  linkedin_urn   text,
  full_name      text,
  country        text,                           -- jurisdicción efectiva del contacto
  consent_state  jsonb not null default '{}',    -- {email:{basis,ts,source}, phone:{...}}
  attributes     jsonb not null default '{}',
  created_at     timestamptz not null default now()
);
create unique index on person (tenant_id, email) where email is not null;

create table membership (
  id          uuid primary key default uuid_generate_v4(),
  tenant_id   uuid not null references tenant(id),
  person_id   uuid not null references person(id),
  account_id  uuid not null references account(id),
  title       text,
  seniority   text,
  department  text,
  buying_role text,                              -- champion|economic|technical|user|blocker
  started_at  date,
  ended_at    date,                              -- null = vigente; habilita job-change signals
  unique (tenant_id, person_id, account_id, started_at)
);

create table signal (
  id           uuid primary key default uuid_generate_v4(),
  tenant_id    uuid not null references tenant(id),
  entity_type  text not null check (entity_type in ('account','person')),
  entity_id    uuid not null,
  type         text not null,                    -- ver docs/06
  strength     numeric(4,3) not null,            -- 0..1 normalizado
  half_life_h  int not null,                     -- decay del valor
  source       text not null,
  legal_basis  text not null,                    -- legitimate_interest|consent|contract
  payload      jsonb not null default '{}',
  observed_at  timestamptz not null,
  ingested_at  timestamptz not null default now()
);
create index on signal (tenant_id, entity_id, observed_at desc);
create index on signal (tenant_id, type, observed_at desc);

create table program (
  id            uuid primary key default uuid_generate_v4(),
  tenant_id     uuid not null references tenant(id),
  key           text not null,
  version       text not null,                   -- semver
  spec          jsonb not null,                  -- DSL compilado
  spec_hash     text not null,
  status        text not null check (status in ('draft','staged','live','paused','archived')),
  created_by    text,
  created_at    timestamptz not null default now(),
  unique (tenant_id, key, version)
);

create table enrollment (
  id              uuid primary key default uuid_generate_v4(),
  tenant_id       uuid not null references tenant(id),
  program_id      uuid not null references program(id),
  entity_type     text not null,
  entity_id       uuid not null,
  variant         text not null default 'treatment', -- treatment|control
  score           numeric(6,3),
  tier            text,                               -- t1|t2|t3
  state           text not null,
  entered_at      timestamptz not null default now(),
  exited_at       timestamptz,
  exit_reason     text,
  unique (tenant_id, program_id, entity_type, entity_id)
);

create table touch (
  id              uuid primary key default uuid_generate_v4(),
  tenant_id       uuid not null references tenant(id),
  enrollment_id   uuid references enrollment(id),
  channel         text not null,                 -- email|linkedin|ads|voice|sms|task|webhook
  direction       text not null default 'out',
  step_key        text,
  idempotency_key text not null,
  content_ref     uuid,                           -- contenido generado + trazabilidad de modelo
  provider        text,
  status          text not null,                  -- queued|sent|delivered|bounced|opened|replied|failed
  cost_micros     bigint not null default 0,
  sent_at         timestamptz,
  unique (tenant_id, idempotency_key)
);
create index on touch (tenant_id, enrollment_id, sent_at desc);

create table outcome (
  id            uuid primary key default uuid_generate_v4(),
  tenant_id     uuid not null references tenant(id),
  enrollment_id uuid references enrollment(id),
  account_id    uuid references account(id),
  type          text not null,                    -- reply_positive|meeting|opp_created|won|expansion|churn
  value_micros  bigint,
  occurred_at   timestamptz not null,
  source        text not null                     -- crm|manual|inferred
);

create table policy_decision (
  id            uuid primary key default uuid_generate_v4(),
  tenant_id     uuid not null references tenant(id),
  subject_type  text not null,
  subject_id    uuid not null,
  action        text not null,
  decision      text not null check (decision in ('allow','deny','review')),
  rule_key      text not null,
  jurisdiction  text,
  rationale     text,
  decided_at    timestamptz not null default now()
);

create table cost_event (
  id            uuid primary key default uuid_generate_v4(),
  tenant_id     uuid not null references tenant(id),
  program_id    uuid references program(id),
  kind          text not null,                    -- enrichment|llm|send|ads|storage
  provider      text,
  units         numeric not null,
  cost_micros   bigint not null,
  billed_credits numeric not null default 0,
  occurred_at   timestamptz not null default now()
);
create index on cost_event (tenant_id, program_id, occurred_at);

-- RLS (patrón aplicado a todas las tablas)
alter table account enable row level security;
create policy tenant_isolation on account
  using (tenant_id = current_setting('zolts.tenant_id')::uuid);
