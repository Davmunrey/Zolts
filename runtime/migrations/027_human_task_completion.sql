-- A human task, and whether anybody did it.
--
-- `worker._dispatch` records a manual step as a touch with `status = 'queued'`
-- and `content.awaiting = 'human_review'`, then succeeds the action. Every code
-- path that moves a touch off `queued` lives in `inbound.py` and is driven by a
-- *provider* event — opened, replied, bounced. A `task` or `voice` touch has no
-- provider, so no such event will ever arrive: the work item was created and
-- nothing in the system could ever close it (D-83).
--
-- `spec.plays.*.steps.sla_hours` was read by nothing (D-84), and could not have
-- been: an SLA measured against a state nothing leaves reports every task as
-- breached for ever, which is a measurement that never measures — the shape
-- D-76 found in a guardrail whose control arm is structurally zero.
--
-- Three columns rather than a new `status` value, following the precedent
-- migration 013 set with `complained_at` and `unsubscribed_at`: the fact and
-- its time, on the row that carries the work, leaving a CHECK constraint other
-- code branches on undisturbed.
alter table touch add column due_at       timestamptz;
alter table touch add column completed_at timestamptz;
alter table touch add column completed_by text;

-- The deadline is stamped when the task is created, from the step's declared
-- `sla_hours`, rather than computed at read time by walking the programme's
-- spec. A task created under a four-hour SLA keeps its four-hour deadline when
-- the programme is republished with twenty-four — the same reasoning that
-- freezes a metric with the version that declared it (`docs/10`), applied to
-- the promise a human made about response time.

-- The question an operator asks is "what is open, oldest first", and the
-- question a breach report asks is "what is open and past due". Partial on
-- `completed_at is null`, because a completed task is never either.
create index touch_open_task_ix on touch (tenant_id, due_at)
  where completed_at is null and due_at is not null;
