-- The ceiling that makes overage reachable, and bounded.
--
-- `docs/18` I6 settled this: "overage with an 80% alert and a configurable
-- hard ceiling". The runtime had the hard part and not the configurable one —
-- it stopped every tenant at their included credits, which is a ceiling that
-- cannot be raised. So overage was priced in the document and unreachable in
-- the product: no tenant could ever consume a credit beyond their plan, and
-- every euro of expansion revenue in `docs/12` was arithmetic nobody could run.
--
-- Null means the included allowance, which keeps the default behaviour exactly
-- as it was: a tenant cannot overspend by accident. Raising it is a deliberate
-- act, and a deliberate act is what overage is meant to bill for.
alter table tenant add column credit_ceiling numeric(14,2)
  check (credit_ceiling is null or credit_ceiling >= 0);

-- Raising the ceiling is provisioning, like changing a plan. The role that
-- serves requests cannot write to `tenant` at all, so this needs no policy of
-- its own — it inherits the one that already refuses.
comment on column tenant.credit_ceiling is
  'Hard spend ceiling in credits. Null means the plan''s included allowance.';
