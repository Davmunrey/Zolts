#!/usr/bin/env bash
# Restore a Zolts database from a pg_dump, and prove the result actually runs.
#
#   scripts/restore.sh backup.sql "$OWNER_URL" "$APP_PASSWORD"
#
# The step this exists for is the first one. `pg_dump` of a single database
# emits `GRANT ... TO zolts_app` and no `CREATE ROLE`, because roles are
# cluster-wide. Restored into a fresh managed project the role does not exist,
# every GRANT fails — and `psql` without ON_ERROR_STOP exits 0 anyway. The
# restore reports success and the application role has no privileges: the API
# starts, connects, and cannot read a single row.
set -euo pipefail

DUMP="${1:?usage: restore.sh <dump.sql> <owner-url> <app-password>}"
OWNER_URL="${2:?owner connection URL}"
APP_PASSWORD="${3:?password for the zolts_app role}"
APP_ROLE="${APP_ROLE:-zolts_app}"

echo "1/4  creating the ${APP_ROLE} role if it is absent"
psql "$OWNER_URL" -v ON_ERROR_STOP=1 -q <<SQL
do \$\$
begin
  if not exists (select 1 from pg_roles where rolname = '${APP_ROLE}') then
    execute format('create role %I login password %L', '${APP_ROLE}', '${APP_PASSWORD}');
  end if;
end
\$\$;
SQL

echo "2/4  restoring, stopping on the first error"
# ON_ERROR_STOP is the difference between a restore and the appearance of one.
psql "$OWNER_URL" -v ON_ERROR_STOP=1 -q -f "$DUMP"

echo "3/4  re-granting and applying any newer migration"
ZOLTS_DATABASE_URL="$OWNER_URL" python3 -m runtime.cli migrate

echo "4/4  checking the result can take traffic"
APP_URL="$(python3 - "$OWNER_URL" "$APP_ROLE" "$APP_PASSWORD" <<'PY'
import sys
from urllib.parse import quote, urlsplit, urlunsplit

url, role, password = sys.argv[1], sys.argv[2], sys.argv[3]
parts = urlsplit(url)
host = parts.hostname or ""
if parts.port:
    host = f"{host}:{parts.port}"
print(urlunsplit((parts.scheme, f"{quote(role)}:{quote(password)}@{host}",
                  parts.path, parts.query, parts.fragment)))
PY
)"
ZOLTS_DATABASE_URL="$OWNER_URL" ZOLTS_APP_DATABASE_URL="$APP_URL" \
  python3 -m runtime.cli preflight

echo
echo "restored. A restore that has not been preflighted is a backup nobody has tested."
