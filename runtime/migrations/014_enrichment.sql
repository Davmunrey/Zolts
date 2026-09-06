-- Enrichment: the six-eighths of the price list the runtime could not execute.
--
-- `docs/12` prices eight billable actions. Two of them ran. The three
-- enrichment actions — a verified email at 8 credits, a mobile at 25,
-- firmographics at 4 — are the bulk of the consumption the pricing model
-- assumes, and none of them existed. A Growth tenant paying EUR 1,490 for
-- 60,000 credits could physically spend about a tenth of them.
--
-- `zolts/waterfall.py` has been the cost optimiser `docs/07` calls the margin
-- lever and `docs/15` names as the answer to a provider shock, and nothing in
-- the runtime imported it.

-- A phone number is a first-class column and not an attribute, for one
-- reason that settles it: national do-not-call registries are matched on the
-- number. A phone hidden in `attributes` is a phone that cannot be suppressed,
-- and the policy engine already reasons about voice, sms and whatsapp.
alter table person add column phone text;
-- The same vocabulary `email_status` already uses, plus the one state only a
-- phone has. Two spellings of "we checked and it is good" on one table is how
-- a query silently misses half the rows it was written for.
alter table person add column phone_status text
  check (phone_status is null or phone_status in
         ('verified','risky','invalid','unknown','do_not_call'));
create index person_phone_ix on person (tenant_id, phone) where phone is not null;

-- The providers this tenant may spend money with. Per tenant rather than
-- global: a partner brings their own contracts, and their rates are theirs.
create table data_provider (
  id             uuid primary key default gen_random_uuid(),
  tenant_id      uuid not null references tenant(id) on delete cascade,
  key            text not null,
  -- Which fields it can resolve. A provider that sells emails and phones is
  -- one row with two fields, because it is one contract and one credential.
  fields         text[] not null check (cardinality(fields) > 0),
  connection_id  uuid references connection(id) on delete set null,
  -- What a call costs us, in micros of EUR. The waterfall optimises against
  -- this, so a wrong number here buys the wrong provider order.
  unit_cost_micros bigint not null check (unit_cost_micros >= 0),
  -- Share of returned values that are correct, as contracted or as measured.
  accuracy       numeric(4,3) not null default 0.900
                 check (accuracy > 0 and accuracy <= 1),
  -- Some providers bill for a lookup that returns nothing. It changes the
  -- optimal order, so it is a property and not a footnote.
  billed_on_miss boolean not null default false,
  -- A conservative starting hit rate, used only until this provider has made
  -- enough calls in a cohort for its own measurements to mean anything.
  default_hit_rate numeric(4,3) not null default 0.300
                 check (default_hit_rate >= 0 and default_hit_rate <= 1),
  config         jsonb not null default '{}',
  enabled        boolean not null default true,
  created_at     timestamptz not null default now(),
  unique (tenant_id, key)
);

-- Every attempt, hit or miss. This table is the per-provider per-cohort
-- hit-rate matrix `docs/07` says accrues only with real volume and `docs/15`
-- lists among the things a competitor cannot buy. It is also the provenance
-- record: the latest hit for an entity and a field is where that value came
-- from, so one table answers both "which provider should go first" and "where
-- did this phone number come from", and the two answers cannot disagree.
create table enrichment_attempt (
  id             uuid primary key default gen_random_uuid(),
  tenant_id      uuid not null references tenant(id) on delete cascade,
  provider_key   text not null,
  field          text not null check (field in ('email','phone','firmographics')),
  -- The segment the hit rate is scored in. Coarse on purpose: a matrix keyed
  -- too finely never accumulates enough calls per cell to mean anything.
  cohort         text not null,
  entity_type    text not null check (entity_type in ('account','person')),
  entity_id      uuid not null,
  hit            boolean not null,
  confidence     numeric(4,3),
  cost_micros    bigint not null default 0,
  -- Why this lookup was lawful. Enrichment obtains personal data from a third
  -- party; a resolved value with no basis recorded is one nobody can defend.
  legal_basis    text,
  attempted_at   timestamptz not null default now()
);
create index enrichment_attempt_matrix on enrichment_attempt
  (tenant_id, provider_key, field, cohort, attempted_at desc);
create index enrichment_attempt_provenance on enrichment_attempt
  (tenant_id, entity_type, entity_id, field, attempted_at desc) where hit;

alter table data_provider enable row level security;
alter table data_provider force row level security;
create policy data_provider_tenant_isolation on data_provider
  using (tenant_id = zolts_internal.current_tenant())
  with check (tenant_id = zolts_internal.current_tenant());

alter table enrichment_attempt enable row level security;
alter table enrichment_attempt force row level security;
create policy enrichment_attempt_tenant_isolation on enrichment_attempt
  using (tenant_id = zolts_internal.current_tenant())
  with check (tenant_id = zolts_internal.current_tenant());
