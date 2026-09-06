-- Whether a conversion was read, or assumed.
--
-- Every reply used to be recorded as `reply_positive`. The triage agent now
-- classifies the ones that arrive with a body, but a provider that sends none
-- still lands as a positive, unchanged and on purpose: flipping that default
-- would move every existing tenant's measured lift on a deploy, silently.
--
-- So the over-count is not hidden, it is counted. A conversion knows whether
-- somebody read the words behind it, and the measurement says how much of the
-- reported lift rests on ones nobody did.
--
-- Null is the honest default for every row already written: those replies were
-- never classified, and back-filling them as verified would be a lie recorded
-- in a migration.
alter table outcome add column verified_by text;

comment on column outcome.verified_by is
  'What established this outcome, when anything did. `triage` means an agent '
  'read the reply text and classified it. Null means the outcome was recorded '
  'from a provider event alone — for a reply, that it arrived, not what it '
  'said.';

-- The measurement reads this per program on every render.
create index outcome_unverified on outcome (tenant_id, type)
  where verified_by is null;
