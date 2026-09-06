-- Row-level security.
--
-- Three properties, in order of importance:
--
-- 1. A query that forgets to declare a tenant raises. It does not quietly
--    return an empty set, because an empty set is indistinguishable from a
--    correct answer and a silent isolation bug survives every test that
--    asserts on results rather than on errors.
-- 2. Policies are FORCED, so the owning role is bound by them too. Without
--    this, migrations and application code see different databases.
-- 3. The privileged surface is two SECURITY DEFINER functions, both of which
--    return identifiers only. Everything else the application does runs as a
--    role that cannot bypass RLS.

-- The internal schema is deliberately NOT named `zolts`. Postgres resolves
-- `"$user"` first in the default search_path, so a schema sharing a name with
-- the connecting role shadows `public` for every unqualified object. A schema
-- named `zolts` plus a role named `zolts` made a second migration run create a
-- complete duplicate of every table under it, silently, and report success.
-- Every connection also pins its search_path; this name is the second lock.
create schema if not exists zolts_internal;

create function zolts_internal.current_tenant() returns uuid
language plpgsql stable as $$
declare v text;
begin
  v := current_setting('zolts.tenant_id', true);
  if v is null or v = '' then
    raise exception 'zolts.tenant_id is not set for this transaction'
      using errcode = '42501';
  end if;
  return v::uuid;
end $$;

do $$
declare t text;
begin
  foreach t in array array[
    'account','person','membership','signal','program','enrollment','touch',
    'outcome','policy_decision','cost_event','suppression','action',
    'connection','api_key','audit_log','mailbox'
  ] loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    execute format(
      'create policy tenant_isolation on %I using (tenant_id = zolts_internal.current_tenant()) '
      'with check (tenant_id = zolts_internal.current_tenant())', t);
  end loop;
end $$;

-- The tenant row itself is readable by its own tenant. RLS is enabled but not
-- forced: provisioning a tenant necessarily happens before a tenant context
-- exists, and that path runs as the owner.
alter table tenant enable row level security;
create policy tenant_self on tenant using (id = zolts_internal.current_tenant());

-- Authentication is the one lookup that cannot be tenant-scoped, because
-- resolving the tenant is its purpose. It is a function rather than a grant so
-- the application can learn a tenant id from a token and nothing else.
create function zolts_internal.resolve_api_key(p_hash text)
returns table (tenant_id uuid, key_id uuid, scopes text[])
language sql security definer set search_path = public, pg_temp as $$
  select k.tenant_id, k.id, k.scopes
  from api_key k
  join tenant t on t.id = k.tenant_id
  where k.token_hash = p_hash
    and k.revoked_at is null
    and t.status = 'active'
$$;

-- Workers poll across tenants; a tenant-scoped transaction cannot. This claims
-- due work under a lease and returns identifiers only. The worker then opens a
-- properly scoped transaction per action to read the payload and act.
--
-- Claimable means: pending and due, or leased with an expired lease. The second
-- case is how a worker that died mid-flight releases its work.
create function zolts_internal.claim_actions(p_owner text, p_lease_seconds int, p_batch int)
returns table (id uuid, tenant_id uuid)
language sql security definer set search_path = public, pg_temp as $$
  with claimable as (
    select a.id from action a
    where a.run_after <= now()
      and (a.state = 'pending'
           or (a.state = 'leased' and a.leased_until < now()))
    order by a.run_after
    for update skip locked
    limit p_batch
  )
  update action a
     set state        = 'leased',
         lease_owner  = p_owner,
         leased_until = now() + make_interval(secs => p_lease_seconds),
         attempts     = a.attempts + 1,
         updated_at   = now()
    from claimable c
   where a.id = c.id
  returning a.id, a.tenant_id
$$;

-- The application role. No BYPASSRLS, no ownership, no DDL.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'zolts_app') then
    create role zolts_app nologin;
  end if;
end $$;

grant usage on schema public, zolts_internal to zolts_app;
grant select, insert, update, delete on all tables in schema public to zolts_app;
revoke insert, update, delete on tenant from zolts_app;
revoke all on schema_migration from zolts_app;
grant execute on function zolts_internal.current_tenant() to zolts_app;
grant execute on function zolts_internal.resolve_api_key(text) to zolts_app;
grant execute on function zolts_internal.claim_actions(text, int, int) to zolts_app;
