-- A program's document metadata: name, blueprint, owner, description.
--
-- Only `spec` was persisted, so the blueprint was lost at publish time and the
-- console rendered "null" in its own column. The blueprint is not decoration:
-- the linter scopes rules by it, and the overlay resolver needs it to know
-- which policy a program inherits.
alter table program add column metadata jsonb not null default '{}';

-- Backfill what can be recovered. Rows published before this migration carry
-- no blueprint; the column is nullable in effect, and the console shows an
-- em-dash rather than inventing one.
update program set metadata = jsonb_build_object('key', key, 'version', version)
where metadata = '{}';
