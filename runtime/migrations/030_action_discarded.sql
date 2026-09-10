-- An action a person retired, distinct from one the policy gate cancelled.
--
-- `cancelled` is the runtime's judgement: a decision forbade the action, and
-- the row carries the `policy_decision_id` that says which rule. `discarded`
-- is a person's: the action died after its retries and nobody is going to
-- recover it. Conflating them would put an operator's abandonment in the same
-- bucket as a suppression, and the Policy view would report a decision that
-- was never made.
--
-- Without this state a dead row nobody will act on sits in the worklist
-- forever, and a permanent alarm is one the operator learns to scroll past.

alter table action drop constraint action_state_check;
alter table action add constraint action_state_check
  check (state in ('pending','leased','succeeded','failed','dead','cancelled',
                   'discarded'));

comment on column action.state is
  'pending/leased/succeeded/failed while the runtime owns it; dead once the '
  'retries are spent; cancelled by a policy decision, which names the rule; '
  'discarded by a person who decided the work is not worth recovering';
