-- A policy decision without a reason answers nothing.
--
-- Product invariant 3 says every external action records a policy decision,
-- allow or deny, with a reason. The decision and the rule were enforced; the
-- reason was not. `rationale` was nullable, `record_decision` took
-- `rationale: str | None`, and the whole test suite passed with the gate
-- writing None — found by breaking it on purpose.
--
-- The reason is the answer to the only question a regulated buyer asks about
-- this table: why was this person not contacted. The console's Policy view
-- reads the column straight to the screen, so a null renders as a decision
-- nobody can explain.
--
-- `zolts.policy.PolicyDecision.rationale` is already a non-optional `str`, so
-- the engine has always produced one. Nothing legitimate writes a null, which
-- is why the backfill below is a safety net rather than a data migration.

update policy_decision
   set rationale = 'reason not recorded before migration 018'
 where rationale is null or btrim(rationale) = '';

alter table policy_decision alter column rationale set not null;

alter table policy_decision
  add constraint policy_decision_rationale_not_blank
  check (btrim(rationale) <> '');
