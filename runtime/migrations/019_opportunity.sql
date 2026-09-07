-- The exclusion a B2B GTM product cannot ship without.
--
-- Program 01 has carried this comment since it was written: the clause that
-- excludes accounts with an open deal is *absent*, because there was no
-- `opportunity` table and no sync that would fill one. Writing it anyway would
-- have produced a guard that always passes, which reads as protection and is
-- worse than none. Decision 32.
--
-- Outbound into an account the sales team is already in a deal with is the
-- expensive kind of wrong: it reaches somebody who is already talking to us,
-- through a channel that says nobody is.
--
-- `status` is the connector's answer, not ours. Every CRM names its stages
-- differently — "Closed Won", "won", `IsWon`, a pipeline id — and reading a
-- label we do not own is how a won deal becomes an open one. The connector
-- maps to open/won/lost and declares whether it can; a connector that cannot
-- must not have its silence read as "no open deal", which is what
-- `connection.opportunities_synced_at` below is for.

create table opportunity (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references tenant(id) on delete cascade,
  -- Nullable: a deal whose account this sync has not seen is still a deal, and
  -- dropping it would silently reopen the hole this table exists to close.
  account_id    uuid references account(id) on delete cascade,
  provider      text not null,
  crm_id        text not null,
  name          text,
  -- The provider's own label, kept verbatim so an operator can recognise it.
  stage         text,
  status        text not null default 'open' check (status in ('open', 'won', 'lost')),
  amount_micros bigint,
  currency      text,
  owner         text,
  opened_at     timestamptz,
  closed_at     timestamptz,
  attributes    jsonb not null default '{}',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

-- A sync runs repeatedly and the second run must produce no new rows.
create unique index opportunity_tenant_crm_uq on opportunity (tenant_id, provider, crm_id);

-- The only question an audience asks of this table: does this account have an
-- open deal. A partial index is the answer to that question rather than a
-- general-purpose one, and the audience runs on every enrolment.
create index opportunity_open_ix on opportunity (tenant_id, account_id) where status = 'open';

alter table opportunity enable row level security;
alter table opportunity force row level security;
create policy opportunity_tenant_isolation on opportunity
  using (tenant_id = zolts_internal.current_tenant())
  with check (tenant_id = zolts_internal.current_tenant());

-- Whether a source has ever answered the deals question, and when.
--
-- An empty `opportunity` table is ambiguous: it means either "this tenant has
-- no open deals" or "nothing has ever asked their CRM". The first is a fact
-- and the second is a hole, and `not exists (select 1 from opportunity ...)`
-- reads them identically. This table makes the difference queryable, so an
-- audience that filters on deals can refuse to enrol rather than enrol
-- everybody.
--
-- It is its own table rather than a column on `connection` because a sync does
-- not always have a connection row — a pushed batch from a firewalled CRM is
-- the same sync with no credential to store — and a marker that silently fails
-- to be written is a guard that silently stops guarding.
--
-- One row per provider, because a tenant with two CRMs has one that answers
-- and one that may not. The audience guard asks whether *any* source has
-- delivered deals, which is the right question for the single-CRM tenant this
-- product sells to today and the wrong one for a tenant whose second CRM is
-- deal-blind: their accounts from that CRM would read as having none. Recorded
-- here so the sharper per-account answer is a query change rather than a
-- migration. Decision 32.
create table crm_sync_state (
  tenant_id               uuid not null references tenant(id) on delete cascade,
  provider                text not null,
  opportunities_synced_at timestamptz,
  updated_at              timestamptz not null default now(),
  primary key (tenant_id, provider)
);

alter table crm_sync_state enable row level security;
alter table crm_sync_state force row level security;
create policy crm_sync_state_tenant_isolation on crm_sync_state
  using (tenant_id = zolts_internal.current_tenant())
  with check (tenant_id = zolts_internal.current_tenant());
