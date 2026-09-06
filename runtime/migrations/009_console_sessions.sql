-- A browser session for the operator surface.
--
-- `/console` authenticated by the `x-api-key` header, which a browser cannot
-- send when somebody types the URL: the operator surface returned 401 to an
-- operator, and the quickstart's own instruction was to curl it. A session is
-- what a browser can carry.
--
-- The cookie is NOT the API key. An API key is a long-lived bearer credential
-- that is shown once; putting it in a cookie puts it in browser storage, in
-- history, and in every request to this origin forever. A session token is
-- separate, short-lived, revocable, and useless anywhere but here.
create table console_session (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid        not null references tenant(id) on delete cascade,
  -- The key this session was opened with. Revoking that key must end the
  -- sessions it opened, or revocation is advisory.
  api_key_id   uuid        not null references api_key(id) on delete cascade,
  -- sha256, like every other token in this schema. The value is set in the
  -- cookie once and never stored.
  token_hash   text        not null unique,
  -- Double-submit CSRF token. The cookie is SameSite=Strict, which is already
  -- strong; this is the second lock, and it is cheap.
  csrf_hash    text        not null,
  expires_at   timestamptz not null,
  revoked_at   timestamptz,
  created_ip   text,
  last_seen_at timestamptz,
  created_at   timestamptz not null default now()
);

create index console_session_live on console_session (token_hash)
  where revoked_at is null;
create index console_session_tenant on console_session (tenant_id);

alter table console_session enable row level security;
alter table console_session force row level security;
create policy console_session_tenant_isolation on console_session
  using (tenant_id = zolts_internal.current_tenant())
  with check (tenant_id = zolts_internal.current_tenant());

-- Resolving a session happens before a tenant is known, so it needs the same
-- treatment as resolving an API key: a definer function returning identifiers
-- only, never the row.
create function zolts_internal.resolve_console_session(p_hash text)
returns table (tenant_id uuid, session_id uuid, api_key_id uuid, csrf_hash text)
language sql security definer set search_path = public, pg_temp as $$
  select s.tenant_id, s.id, s.api_key_id, s.csrf_hash
  from console_session s
  join tenant t on t.id = s.tenant_id
  join api_key k on k.id = s.api_key_id
  where s.token_hash = p_hash
    and s.revoked_at is null
    and s.expires_at > now()
    -- A revoked key ends the sessions it opened. Otherwise revocation stops
    -- the credential and leaves the browser holding a working door.
    and k.revoked_at is null
    and t.status = 'active'
$$;

grant execute on function zolts_internal.resolve_console_session(text) to zolts_app;
