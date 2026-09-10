-- When a payload entered this runtime, as distinct from when its signal row
-- was written. `docs/06` targets *ingestion to signal available* at p95 < 5
-- minutes for a Tier A signal and the runtime had no probe for it (SIG-1):
-- `observed_at` is when the source saw the thing, `ingested_at` is when the
-- row appeared, and nothing recorded the moment in between when the payload
-- reached us. The gap is not zero and it grows with the number of live
-- programmes, because `enroll.ingest` evaluates every one of them.
--
-- Nullable, and never back-filled. A row written before this has no arrival
-- time and guessing one would report a latency nobody measured; the report
-- excludes those rows rather than counting them as instantaneous, which is
-- the direction that would flatter the number.
alter table signal add column received_at timestamptz;

-- The report reads the arrival and the write together, per signal type, and
-- only for rows that carry one.
create index signal_received_ix on signal (tenant_id, type, received_at)
  where received_at is not null;
