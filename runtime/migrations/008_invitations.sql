-- Onboarding a partner without giving them a shell.
--
-- Creating a tenant required database access, so every partner cost founder
-- time and the product could not be sold without a human in the loop. An
-- invitation moves the work to the partner while keeping the decision — who
-- gets in — with the operator.
--
-- This table is deliberately NOT tenant-scoped. An invitation exists before
-- its tenant does, so there is no tenant to scope it to, and it is minted by
-- the operator rather than by any tenant. The column recording what it became
-- is `redeemed_tenant_id` rather than `tenant_id`, because it names the tenant
-- this row created and not the tenant that owns this row: giving it the other
-- name would make every isolation check treat a global table as a scoped one.
create table invitation (
  id                 uuid primary key default gen_random_uuid(),
  -- The token is never stored. Same discipline as an API key: sha256 at rest,
  -- shown once at mint time, unrecoverable afterwards.
  token_hash         text        not null unique,
  prefix             text        not null,
  email              citext,
  company_name       text        not null,
  region             text        not null default 'eu',
  blueprint_id       text,
  expires_at         timestamptz not null,
  redeemed_at        timestamptz,
  redeemed_tenant_id uuid        references tenant(id) on delete set null,
  created_by         text,
  created_at         timestamptz not null default now(),

  -- Redemption is all or nothing. A row that claims a tenant must say when,
  -- and a row that says when must name one; anything else is a half-finished
  -- signup that no later query can interpret.
  constraint invitation_redeemed_together check (
    (redeemed_at is null and redeemed_tenant_id is null)
    or (redeemed_at is not null and redeemed_tenant_id is not null))
);

-- The only lookup that matters, on the only path that is unauthenticated.
create index invitation_unredeemed on invitation (token_hash) where redeemed_at is null;
