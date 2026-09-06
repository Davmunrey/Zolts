-- Inbound provider events.
--
-- Stored before they are interpreted. A provider that changes its payload
-- shape, or an interpretation this runtime gets wrong, is then a replay rather
-- than data that never existed: the raw body is the only record of what a
-- provider actually said, and it is the one a compliance question is answered
-- from.

create table inbound_event (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references tenant(id) on delete cascade,
  provider     text not null,
  event_type   text,
  -- The provider's own event id where it has one. Providers retry, and a
  -- retried unsubscribe that recorded a second outcome would corrupt the
  -- measurement it exists to feed.
  external_id  text,
  payload      jsonb not null,
  signature_ok boolean not null,
  handled      boolean not null default false,
  handled_at   timestamptz,
  error        text,
  received_at  timestamptz not null default now()
);
create unique index inbound_event_external_uq on inbound_event (tenant_id, provider, external_id)
  where external_id is not null;
create index inbound_event_unhandled_ix on inbound_event (tenant_id, received_at)
  where not handled;

-- Webhook endpoints are addressed by an opaque token rather than by tenant id.
-- A provider's configuration screen holds a URL, and a URL that carries a
-- tenant id invites enumeration.
create table webhook_endpoint (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references tenant(id) on delete cascade,
  provider     text not null,
  token        text not null unique,
  secret_enc   bytea,
  active       boolean not null default true,
  last_seen_at timestamptz,
  created_at   timestamptz not null default now(),
  unique (tenant_id, provider)
);

alter table inbound_event enable row level security;
alter table inbound_event force row level security;
create policy tenant_isolation on inbound_event
  using (tenant_id = zolts_internal.current_tenant())
  with check (tenant_id = zolts_internal.current_tenant());

alter table webhook_endpoint enable row level security;
alter table webhook_endpoint force row level security;
create policy tenant_isolation on webhook_endpoint
  using (tenant_id = zolts_internal.current_tenant())
  with check (tenant_id = zolts_internal.current_tenant());

-- Resolving a webhook token is the same problem as resolving an API key: it
-- cannot be tenant-scoped, because finding the tenant is its purpose. Same
-- shape of answer — identifiers only.
create function zolts_internal.resolve_webhook(p_token text)
returns table (tenant_id uuid, endpoint_id uuid, provider text, secret_enc bytea)
language sql security definer set search_path = public, pg_temp as $$
  select w.tenant_id, w.id, w.provider, w.secret_enc
  from webhook_endpoint w
  join tenant t on t.id = w.tenant_id
  where w.token = p_token and w.active and t.status = 'active'
$$;

grant select, insert, update, delete on inbound_event, webhook_endpoint to zolts_app;
grant execute on function zolts_internal.resolve_webhook(text) to zolts_app;
