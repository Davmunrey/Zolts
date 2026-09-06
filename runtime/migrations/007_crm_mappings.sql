-- Tenant-authored CRM mappings.
--
-- A partner may run a CRM that is not on the market. No connector written here
-- can read it, so the mapping is the integration and it is stored, versioned
-- and audited like any other piece of configuration a customer authors.
--
-- Product invariant 5, applied to integration: program logic is versioned
-- configuration, never ad-hoc code. A mapping that decides who gets contacted
-- is program logic.

create table crm_mapping (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references tenant(id) on delete cascade,
  provider    text not null,
  name        text not null,
  document    jsonb not null,
  -- Derived from the document at publish time rather than recomputed on read:
  -- the question an audit asks is what was true when the sync ran.
  reads_opt_out boolean not null,
  spec_hash   text not null,
  active      boolean not null default true,
  created_by  text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique (tenant_id, provider)
);
create index crm_mapping_active_ix on crm_mapping (tenant_id) where active;

alter table crm_mapping enable row level security;
alter table crm_mapping force row level security;
create policy tenant_isolation on crm_mapping
  using (tenant_id = zolts_internal.current_tenant())
  with check (tenant_id = zolts_internal.current_tenant());

grant select, insert, update, delete on crm_mapping to zolts_app;
