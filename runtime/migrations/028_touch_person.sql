-- Who a touch actually reached.
--
-- `policy.evaluate` enforces `max_touches_per_person_per_week`, docs/09 calls
-- the frequency cap **global** and docs/06 promises *cross-program
-- deduplication at person level*. None of that was true: the gate counted
-- `touch where enrollment_id = ?`, and an enrolment is on an **account** in
-- every shipped programme. So the cap was neither per person nor global — it
-- was per account per programme, wrong in both directions at once (D-92).
--
-- It could not have been right, because the row did not say who received it.
-- The recipient is resolved at dispatch (`Worker._resolve_contact`) and was
-- thrown away immediately afterwards. This is the column that keeps it.
--
-- Nullable, and deliberately not back-filled. A touch written before this
-- migration cannot be attributed to a person without guessing which contact of
-- the account it went to, and a frequency cap counting a guess is worse than
-- one counting less: it would hold a contact somebody else was sent.
alter table touch add column person_id uuid references person(id) on delete set null;

-- The cap's own query: this tenant's touches to this person inside a window.
-- Partial on `sent_at`, because a touch that was never sent is not one the
-- person received — a policy refusal writes a row with no `sent_at`, and
-- counting it would let a blocked contact consume the budget of an allowed one.
create index touch_person_ix on touch (tenant_id, person_id, sent_at desc)
  where person_id is not null and sent_at is not null;
