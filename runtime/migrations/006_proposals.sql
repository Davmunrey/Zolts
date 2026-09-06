-- Agent proposals.
--
-- Product invariant 1, and ADR-004: agents propose, the runtime disposes. An
-- agent writes a row here; it never writes to `action`. A proposal becomes an
-- action only after the eval gate, the policy gate and the spend guard have
-- each had their say, and the promotion records which of them permitted it.
--
-- The separation is what makes the product sellable to a regulated buyer: the
-- question "who approved this message" has a row as its answer, and the answer
-- distinguishes a human from a threshold.

create table proposal (
  id              uuid primary key default gen_random_uuid(),
  tenant_id       uuid not null references tenant(id) on delete cascade,
  enrollment_id   uuid references enrollment(id) on delete cascade,
  program_id      uuid references program(id) on delete set null,
  agent           text not null,
  step_key        text,
  channel         text,
  -- One proposal per step, for the same reason an action has one: a retried
  -- generation must not produce a second draft competing for approval.
  idempotency_key text not null,

  -- Traceability. An AI Act audit asks which model wrote this, against which
  -- prompt, citing what — and the answer cannot be reconstructed later.
  model           text,
  prompt_version  text,
  content         jsonb not null default '{}',
  evidence        jsonb not null default '[]',

  -- What each gate said. Stored rather than recomputed: the thresholds move,
  -- and the question an audit asks is what was true when it was sent.
  eval            jsonb not null default '{}',
  eval_score      numeric(5,4),
  spend           jsonb not null default '{}',
  cost_micros     bigint not null default 0,

  state           text not null default 'draft'
                  check (state in ('draft','needs_human','approved','rejected',
                                   'dispatched','superseded')),
  gate_reason     text,
  approved_by     text,
  action_id       uuid references action(id) on delete set null,
  created_at      timestamptz not null default now(),
  decided_at      timestamptz,
  unique (tenant_id, idempotency_key)
);
create index proposal_queue_ix on proposal (tenant_id, state, created_at)
  where state in ('draft','needs_human');
create index proposal_enrollment_ix on proposal (tenant_id, enrollment_id, created_at desc);

alter table proposal enable row level security;
alter table proposal force row level security;
create policy tenant_isolation on proposal
  using (tenant_id = zolts_internal.current_tenant())
  with check (tenant_id = zolts_internal.current_tenant());

grant select, insert, update, delete on proposal to zolts_app;
