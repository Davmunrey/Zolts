-- Runtime tables: the transactional outbox, tenant credentials and the audit log.
--
-- Durability model. Every external action is a row in `action` written in the
-- same transaction as the state change that justified it. Workers claim rows
-- under a lease with `for update skip locked`; a worker that dies leaves an
-- expired lease that another worker reclaims. This gives at-least-once delivery
-- with a database as the only piece of infrastructure. Exactly-once at the
-- provider is the connector's job, via the idempotency key carried on the row.

create table action (
  id                 uuid primary key default gen_random_uuid(),
  tenant_id          uuid not null references tenant(id) on delete cascade,
  enrollment_id      uuid references enrollment(id) on delete cascade,
  program_id         uuid references program(id) on delete set null,
  kind               text not null,
  channel            text,
  step_key           text,
  idempotency_key    text not null,
  payload            jsonb not null default '{}',
  state              text not null default 'pending'
                     check (state in ('pending','leased','succeeded','failed','dead','cancelled')),
  attempts           int not null default 0,
  max_attempts       int not null default 5,
  run_after          timestamptz not null default now(),
  leased_until       timestamptz,
  lease_owner        text,
  last_error         text,
  policy_decision_id uuid references policy_decision(id) on delete set null,
  result             jsonb,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  unique (tenant_id, idempotency_key)
);
-- The claim query's only index. Partial, so completed work costs nothing to keep.
create index action_claimable_ix on action (run_after)
  where state in ('pending','leased');
create index action_enrollment_ix on action (tenant_id, enrollment_id, created_at desc);

create table connection (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references tenant(id) on delete cascade,
  provider     text not null,
  display_name text,
  -- Ciphertext only. The runtime refuses to start without the key that opens it,
  -- so a database dump on its own discloses nothing.
  secret_enc   bytea not null,
  config       jsonb not null default '{}',
  status       text not null default 'active' check (status in ('active','revoked','error')),
  last_error   text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (tenant_id, provider, display_name)
);

create table api_key (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references tenant(id) on delete cascade,
  name        text not null,
  -- SHA-256 of the presented token. The token itself is shown once, at creation.
  token_hash  text not null unique,
  prefix      text not null,
  scopes      text[] not null default '{}',
  last_used_at timestamptz,
  revoked_at  timestamptz,
  created_at  timestamptz not null default now()
);
create index api_key_tenant_ix on api_key (tenant_id) where revoked_at is null;

create table audit_log (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references tenant(id) on delete cascade,
  actor      text not null,
  action     text not null,
  subject    text,
  detail     jsonb not null default '{}',
  at         timestamptz not null default now()
);
create index audit_log_ix on audit_log (tenant_id, at desc);

create table mailbox (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         uuid not null references tenant(id) on delete cascade,
  connection_id     uuid references connection(id) on delete set null,
  address           citext not null,
  domain            text not null,
  warmed_days       int not null default 0,
  sent_30d          int not null default 0,
  bounce_rate       numeric(5,4) not null default 0,
  complaint_rate    numeric(6,5) not null default 0,
  reply_rate        numeric(5,4) not null default 0,
  paused            boolean not null default false,
  created_at        timestamptz not null default now(),
  unique (tenant_id, address)
);
create index mailbox_domain_ix on mailbox (tenant_id, domain);
