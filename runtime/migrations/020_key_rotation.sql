-- Name the key that sealed each credential.
--
-- One key seals every connector credential and every webhook secret, for every
-- tenant. That makes replacing it a security control rather than housekeeping:
-- a key that leaks and cannot be replaced is a permanent compromise of every
-- customer's CRM, and the moment to build the replacement is before there is
-- any customer data to replace it under.
--
-- The column holds `runtime.crypto.key_id` — a truncated hash of the key's own
-- hash. It discloses nothing about the key and it turns the only question an
-- operator has mid-rotation, *which rows are still on the old one*, into a
-- `where` clause rather than a decryption pass over every row.
--
-- Null means a row sealed before this migration: openable, unknown, and
-- counted as outstanding until a rotation rewrites it. Not defaulted to the
-- current key, because this migration has no way to know which key that is,
-- and a wrong id is worse than no id — it would report a rotation complete
-- while leaving rows nothing can open.

alter table connection add column secret_key_id text;
alter table webhook_endpoint add column secret_key_id text;
