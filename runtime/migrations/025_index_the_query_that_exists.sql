-- Point the enrichment indexes at the query the runtime actually runs, and
-- remove the one index nothing can ever read.
--
-- `scripts/unused_indexes.py` resets the statistics, runs the whole suite as a
-- workload, and reads `pg_stat_user_indexes.idx_scan` back. Four plain indexes
-- were never scanned once. Three of them were one defect (D-60): a partial
-- index whose predicate is the *complement* of the only query that exists.
--
-- The enrichment sweep asks for the rows that are missing a field:
--
--     select * from person where phone is null order by created_at limit %s
--
-- and `person_phone_ix` covered `where phone is not null` — precisely the rows
-- that query excludes. It could never be chosen, on any volume, for any plan.
-- The index was written from the noun ("we index phone numbers") rather than
-- from the access pattern ("we look for the people who have none").
--
-- These are built for the sweep as written, ordering included, so the planner
-- can walk the index and stop at the limit rather than sorting the table. The
-- `email` and firmographics sweeps had no index at all, which is the same
-- defect with nothing to point at: email is the field the waterfall buys most
-- (ADR-021) and the one a second supplier is being sought for (DATA-1).

drop index if exists person_phone_ix;

create index person_email_missing_ix on person (tenant_id, created_at)
  where email is null;
create index person_phone_missing_ix on person (tenant_id, created_at)
  where phone is null;
create index account_firmographics_missing_ix on account (tenant_id, created_at)
  where employee_band is null or industry_code is null;

-- `action_billed_ix` covered `(tenant_id, program_id, billed_at) where billed_at
-- is not null`. The only statement that reads `billed_at` is the mark itself:
--
--     update action set billed_at = now()
--      where id = %s and billed_at is null and state = 'succeeded'
--
-- which finds its row by primary key and filters on the complementary predicate.
-- No report groups billed actions by programme; a period's charges are counted
-- from the usage ledger, not from this table. The index is a write cost on the
-- highest-volume table in the schema — one row per programme step per enrolment
-- — bought for a read that does not exist.
drop index if exists action_billed_ix;
