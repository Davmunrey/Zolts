-- Charging for the step the runtime actually ran.
--
-- `docs/12` prices program execution at 0.2 credits a step. It is the
-- highest-volume action in the price list — a six-step play over ten thousand
-- accounts is sixty thousand of them — and the runtime metered none of it. The
-- base unit of consumption was free, so every program running today produced
-- margin nobody invoiced.
--
-- The column goes on `action` rather than into a separate ledger because the
-- action *is* the step: an outbox row that already carries the idempotency key
-- making that step unique within its enrollment. A retried action therefore
-- bills once by construction, and the question "was this step charged" is
-- answered by looking at the step rather than by joining two tables and hoping
-- they agree.
--
-- Null means not billed, and there are three honest ways to be null: the step
-- has not run yet, it was refused by the policy gate, or it was deferred for
-- budget or capacity. All three are steps the customer does not owe for.

alter table action add column billed_at timestamptz;

-- Answers "what has this program cost so far" without scanning every action
-- ever queued. Partial, because the unbilled rows are the majority and are
-- never the ones being summed.
create index action_billed_ix on action (tenant_id, program_id, billed_at)
  where billed_at is not null;
