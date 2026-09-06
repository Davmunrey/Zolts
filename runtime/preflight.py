"""What must be true before a deployment takes traffic.

Every check here corresponds to a property this runtime claims. A managed host
is where those claims stop being enforced by a test suite and start being
enforced by whoever typed the environment variables, so they are checked once,
out loud, against the database that is actually connected.

The checks are ordered by what they cost to get wrong. A missing secret key is
recoverable. A production deployment whose application role can bypass row-level
security is one query away from showing one customer another customer's pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from runtime.config import Settings
from runtime.db import Database, is_pooled

# Values that appear in this repository's own examples and CI. A deployment
# using one of them is not configured; it is running the documentation.
KNOWN_DEV_SECRETS = frozenset({
    "dev-secret", "test-secret-key", "ci-only-not-a-real-key", "changeme", "secret",
})

MIN_SECRET_LENGTH = 32


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    # A failed check that is not fatal still deploys. It is the difference
    # between "this will hurt someone" and "this will annoy you".
    fatal: bool = True


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str, *, fatal: bool = True) -> None:
        self.checks.append(Check(name, ok, detail, fatal))

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    @property
    def blocking(self) -> list[Check]:
        return [c for c in self.failures if c.fatal]

    def as_dict(self) -> dict[str, Any]:
        return {"ready": not self.blocking,
                "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail,
                            "fatal": c.fatal} for c in self.checks]}


def run(settings: Settings, db: Database) -> Report:
    report = Report()
    production = settings.environment == "production"

    _secret(report, settings, production)
    _reachable(report, db)
    if not report.checks[-1].ok:
        # Nothing below this line can be answered without a database.
        return report

    _encryption_in_transit(report, db, settings, production)
    _application_role(report, settings, db, production)
    _migrations(report, db)
    _forced_rls(report, db)
    _isolation(report, db)
    _pooling(report, settings)
    _behaviour_flags(report, settings, production)
    return report


def _secret(report: Report, settings: Settings, production: bool) -> None:
    key = settings.secret_key
    if key in KNOWN_DEV_SECRETS:
        report.add("secret key", False,
                   "ZOLTS_SECRET_KEY is a value published in this repository. It seals "
                   "connector credentials, so every credential in this database is "
                   "readable by anyone holding a copy of the source", fatal=production)
    elif len(key) < MIN_SECRET_LENGTH:
        report.add("secret key", False,
                   f"ZOLTS_SECRET_KEY is {len(key)} characters; use at least "
                   f"{MIN_SECRET_LENGTH} (openssl rand -hex 32)", fatal=production)
    else:
        report.add("secret key", True, f"{len(key)} characters, not a published value")


def _reachable(report: Report, db: Database) -> None:
    try:
        with db.admin_tx() as cur:
            cur.execute("select version() as v, current_user as u")
            row = cur.fetchone()
        report.add("database", True,
                   f"{row['v'].split(' on ')[0]}, connected as {row['u']}")
    except Exception as exc:  # noqa: BLE001 - preflight reports, it does not raise
        report.add("database", False, f"cannot connect: {exc}")


LOCAL_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1", "[::1]"})


def _is_local(url: str) -> bool:
    """Whether the database is reached without crossing a network.

    A Unix socket or loopback connection is not encrypted and does not need to
    be. Treating that as a failure would make the check noise, and a check that
    is noise is a check that gets ignored on the day it is right.
    """
    from urllib.parse import urlsplit

    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    return host.lower() in LOCAL_HOSTS


def _encryption_in_transit(report: Report, db: Database, settings: Settings,
                           production: bool) -> None:
    """A managed database is reached across the public internet."""
    try:
        with db.admin_tx() as cur:
            cur.execute("select ssl, version from pg_stat_ssl where pid = pg_backend_pid()")
            row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        report.add("encryption in transit", False, f"could not determine: {exc}",
                   fatal=False)
        return
    if row and row["ssl"]:
        report.add("encryption in transit", True, f"TLS {row['version']}")
        return
    if _is_local(settings.database_url):
        report.add("encryption in transit", True,
                   "not encrypted, and not crossing a network: the database is local")
        return
    report.add("encryption in transit", False,
               "the connection is not encrypted and the database is not local. A "
               "managed database is reached over the public internet; add "
               "sslmode=require to the URL", fatal=production)


def _application_role(report: Report, settings: Settings, db: Database,
                      production: bool) -> None:
    """The second lock: a role that cannot bypass row-level security."""
    if not settings.isolation_enforced:
        report.add("application role", False,
                   "ZOLTS_APP_DATABASE_URL is unset, so the application connects as the "
                   "owner. Row-level security is FORCED and still applies, but the role "
                   "that could bypass it is the one serving requests", fatal=production)
        return
    try:
        with db.pool.connection() as conn:
            row = conn.execute(
                "select current_user as u, rolsuper, rolbypassrls from pg_roles"
                " where rolname = current_user").fetchone()
    except Exception as exc:  # noqa: BLE001
        report.add("application role", False, f"cannot inspect: {exc}")
        return
    user, is_super, bypasses = row
    if is_super or bypasses:
        report.add("application role", False,
                   f"'{user}' has {'SUPERUSER' if is_super else 'BYPASSRLS'}, so every "
                   "row-level security policy is advisory for the role serving requests")
    else:
        report.add("application role", True,
                   f"'{user}': not a superuser, cannot bypass row-level security")


def _migrations(report: Report, db: Database) -> None:
    try:
        with db.admin_tx() as cur:
            cur.execute("select version from schema_migration order by 1")
            applied = [r["version"] for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        report.add("migrations", False, f"cannot read the ledger: {exc}")
        return
    from runtime.db import MIGRATIONS

    on_disk = sorted(p.stem for p in MIGRATIONS.glob("*.sql"))
    missing = [v for v in on_disk if v not in applied]
    if missing:
        report.add("migrations", False,
                   f"{len(missing)} not applied: {', '.join(missing)}. Run "
                   "'python3 -m runtime.cli migrate'")
    else:
        report.add("migrations", True, f"{len(applied)} applied, none pending")


def _forced_rls(report: Report, db: Database) -> None:
    """FORCE, not ENABLE. ENABLE alone exempts the table's owner."""
    try:
        with db.admin_tx() as cur:
            cur.execute(
                "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace"
                " join information_schema.columns col on col.table_name = c.relname"
                "   and col.table_schema = n.nspname and col.column_name = 'tenant_id'"
                " where n.nspname = 'public' and c.relkind = 'r'"
                "   and not (c.relrowsecurity and c.relforcerowsecurity)")
            unprotected = [r["relname"] for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        report.add("forced row-level security", False, f"cannot inspect: {exc}")
        return
    if unprotected:
        report.add("forced row-level security", False,
                   f"{len(unprotected)} tenant-scoped tables are not FORCE-protected: "
                   f"{', '.join(sorted(unprotected))}")
    else:
        report.add("forced row-level security", True,
                   "every tenant-scoped table is FORCE-protected")


def _isolation(report: Report, db: Database) -> None:
    """The claim, tested rather than asserted: no tenant means no rows.

    The probe is expected to fail, so the transaction is left to unwind on its
    own. An explicit rollback here was calling a method psycopg's transaction
    object does not have, and an outer catch made the whole check pass by
    accident — right answer, wrong reason, and one refactor from being wrong.
    """
    refused = False
    detail = ""
    try:
        with db.pool.connection() as conn:
            with conn.transaction():
                try:
                    conn.execute("select 1 from account limit 1").fetchone()
                except Exception as exc:  # noqa: BLE001 - this is the pass condition
                    refused = True
                    detail = str(exc).strip().splitlines()[0]
                raise _Probe
    except _Probe:
        pass
    except Exception as exc:  # noqa: BLE001
        report.add("unscoped reads refused", False, f"could not be checked: {exc}")
        return

    if refused:
        report.add("unscoped reads refused", True,
                   f"a query with no tenant raises rather than returning rows: {detail}")
    else:
        report.add("unscoped reads refused", False,
                   "a query with no tenant set returned without raising, so a code "
                   "path that forgets the tenant reads across tenants")


class _Probe(Exception):
    """Unwinds the probe's transaction without leaving anything behind."""


def _pooling(report: Report, settings: Settings) -> None:
    pooled = is_pooled(settings.app_database_url)
    report.add("connection endpoint", True,
               "transaction pooler: prepared statements disabled, tenant scoping is "
               "transaction-local and unaffected" if pooled
               else "direct endpoint, with this runtime's own pool in front of it")


def _behaviour_flags(report: Report, settings: Settings, production: bool) -> None:
    if settings.dry_run:
        report.add("dry run", False,
                   "ZOLTS_DRY_RUN is true, so nothing is ever sent. Correct for a "
                   "rehearsal, and silent for a launch", fatal=production)
    else:
        report.add("dry run", True, "off: actions reach their providers")
    report.add("agents", True,
               "enabled" if settings.agents_enabled
               else "disabled; non-agent programs run unchanged")


def render(report: Report) -> str:
    lines = []
    for check in report.checks:
        mark = "OK  " if check.ok else ("FAIL" if check.fatal else "WARN")
        lines.append(f"{mark}  {check.name}: {check.detail}")
    lines.append("")
    if report.blocking:
        lines.append(f"NOT READY: {len(report.blocking)} blocking "
                     f"{'check' if len(report.blocking) == 1 else 'checks'}")
    else:
        warnings = len(report.failures)
        lines.append("READY" + (f", with {warnings} warning"
                                f"{'' if warnings == 1 else 's'}" if warnings else ""))
    return "\n".join(lines)
