-- The jurisdiction pack that decided, kept so a decision can be reproduced.
--
-- `policy_decision` recorded a rule key, a jurisdiction and a reason, and the
-- rules themselves lived in `zolts/policy.py` — code, changing with every
-- release. So a decision taken in March resolved to whatever the pack said
-- today, and the one question the table exists to answer ("under which rule
-- was this person contacted?") had no answer anybody could check (D-53).
--
-- Not tenant-scoped: a jurisdiction's rules are the same for every tenant,
-- and a tenant's own tightening is its program's `policy.overrides`, which is
-- versioned with the program (ADR-030). Written once per digest: publishing
-- the same document twice is the same pack, and publishing a different one is
-- a new version that new decisions cite while old decisions keep citing the
-- old one.
create table policy_pack (
  version      text not null,
  digest       text primary key,
  -- Every field a rule decides by, in the form `zolts.policy.to_document`
  -- produces. A body that omitted one would hash the same across a change to
  -- it, and the digest would certify a rule it never covered.
  body         jsonb not null,
  -- Which pack a new decision cites. Exactly one, enforced below: two active
  -- packs is a runtime where the answer depends on which row was read first.
  active       boolean not null default false,
  published_by text not null,
  published_at timestamptz not null default now()
);

create unique index policy_pack_one_active on policy_pack (active) where active;

-- Every decision names the pack that produced it. Nullable because rows
-- written before this migration cannot be attributed to one — and saying so
-- is the honest reading, rather than back-filling them with today's digest
-- and claiming a provenance nobody has.
alter table policy_decision add column pack_version text;
alter table policy_decision add column pack_digest text;
