-- The research dossier: the last action `docs/12` priced and nothing produced.
--
-- `docs/08` gives the Researcher a role — account plus signals in, a dossier
-- with citations and sources out — and the runtime had no such artefact. The
-- word appeared once, as a local variable inside the copywriter's own path:
-- evidence assembled for one message and thrown away when the message was
-- written. At 20 credits it is the most expensive thing in the price list and
-- the one a buyer asks to see.
--
-- Stored rather than derived, for the reason it costs money: a dossier is the
-- output of a model call, and recomputing one to render a page would spend
-- twenty credits on a refresh.

create table dossier (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references tenant(id) on delete cascade,
  account_id   uuid not null references account(id) on delete cascade,

  -- `complete` means every claim in it carries a citation. `thin` means the
  -- verifier removed enough that a person should read it before it is used —
  -- recorded rather than discarded, because a thin dossier on an account with
  -- no data is a data problem and deleting the evidence of it hides that.
  -- `refused` means no dossier was produced: the guard, the model or the
  -- absence of any evidence at all. It is stored so the next caller learns
  -- why without paying to find out again.
  state        text not null check (state in ('complete', 'thin', 'refused')),
  body         text not null default '',

  -- What it was allowed to say, and what was struck out. The dropped claims
  -- are the audit trail `docs/08` requires: the interesting artefact is not
  -- the paragraph that shipped but the sentence the verifier removed.
  evidence     jsonb not null default '[]'::jsonb,
  dropped      jsonb not null default '[]'::jsonb,

  model          text,
  prompt_version text,
  cost_micros    bigint not null default 0,
  billed_credits numeric(12,4) not null default 0,

  -- A dossier is current only for the facts it saw. `built_through` is the
  -- newest signal it read, so "is this still the answer" is a comparison
  -- rather than a guess, and a funding round that arrived afterwards makes it
  -- stale without anybody having to remember to invalidate anything.
  built_through timestamptz,
  signals_seen  integer not null default 0,
  created_at    timestamptz not null default now(),

  -- Which of two dossiers is the newer one. `created_at` cannot answer that:
  -- `now()` is the *transaction* timestamp, so a rewrite in the same
  -- transaction as the original shares its value exactly and "order by
  -- created_at desc limit 1" then returns whichever row the planner reached
  -- first. For a table whose whole purpose is "the current answer for this
  -- account", an undecidable ordering is a wrong answer.
  seq           bigserial not null
);

-- The question is always "the current dossier for this account", so the index
-- is the answer to it rather than a general-purpose one.
create index dossier_current on dossier (tenant_id, account_id, seq desc);

alter table dossier enable row level security;
alter table dossier force row level security;
create policy dossier_tenant_isolation on dossier
  using (tenant_id = zolts_internal.current_tenant())
  with check (tenant_id = zolts_internal.current_tenant());
