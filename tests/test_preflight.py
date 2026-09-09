"""What a deployment is checked for before it takes traffic.

These run against a real database, because every check here is a claim about a
real database. A preflight verified against a stub would report that isolation
holds without anything having tested it — which is the exact failure the check
exists to prevent.
"""

from __future__ import annotations

import contextlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

from runtime import preflight
from runtime.config import Settings
from runtime.db import Database
from tests.conftest import requires_app_role, APP_URL, OWNER_URL, requires_db


def _plaintext(url: str) -> str:
    """The same database, reached without TLS."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "sslmode"]
    query.append(("sslmode", "disable"))
    return urlunsplit(parts._replace(query=urlencode(query)))


@contextlib.contextmanager
def _unencrypted_db():
    """A database handle whose connection is provably not encrypted.

    Both no-TLS branches of the check need one, and neither used to ask for it
    — they inherited whatever the ambient server did. A Debian-packaged
    Postgres ships `ssl = on`; the `postgres:16` container CI runs does not. So
    the same two tests exercised the *local* branch on one machine and the
    *TLS* branch on the other: green on CI, red on a contributor's laptop,
    against a product that is correct on both. A test that fails on correct
    code teaches the team to press re-run, which is worth less than no test
    (D-35).

    `sslmode=disable` makes the premise the test's own, and the assertion below
    fails loudly rather than quietly proving a different branch, if some future
    server refuses a plaintext connection.
    """
    database = Database(_plaintext(OWNER_URL or ""), _plaintext(APP_URL or ""))
    try:
        with database.admin_tx() as cur:
            cur.execute("select ssl from pg_stat_ssl where pid = pg_backend_pid()")
            assert cur.fetchone()["ssl"] is False, (
                "this connection negotiated TLS despite sslmode=disable, so the "
                "test would exercise the TLS branch and assert nothing about the "
                "branch it names")
        yield database
    finally:
        database.close()


def _settings(**overrides) -> Settings:
    base = dict(database_url=OWNER_URL or "", app_database_url=APP_URL or "",
                secret_key="a" * 40, environment="production", lease_seconds=60,
                worker_batch=25, dry_run=False, agents_enabled=False)
    return Settings(**{**base, **overrides})


def _named(report: preflight.Report, name: str) -> preflight.Check:
    match = [c for c in report.checks if c.name == name]
    assert match, f"no check named {name!r}; got {[c.name for c in report.checks]}"
    return match[0]


@requires_app_role
def test_a_correct_deployment_is_ready(db, app_role_is_restricted):
    report = preflight.run(_settings(), db)
    assert not report.blocking, [c.detail for c in report.blocking]
    assert _named(report, "forced row-level security").ok
    assert _named(report, "unscoped reads refused").ok
    assert _named(report, "migrations").ok


@requires_db
def test_a_published_secret_blocks_production(db):
    """The value is in this repository, in CI, and in the compose file. A
    deployment using it has not been configured; it is running the docs."""
    report = preflight.run(_settings(secret_key="dev-secret"), db)
    check = _named(report, "secret key")
    assert not check.ok and check.fatal
    assert report.blocking


@requires_app_role
def test_a_published_secret_is_only_a_warning_outside_production(db, app_role_is_restricted):
    report = preflight.run(_settings(secret_key="dev-secret",
                                     environment="development"), db)
    check = _named(report, "secret key")
    assert not check.ok
    assert not check.fatal
    assert not report.blocking


@requires_db
def test_a_short_secret_is_refused(db):
    report = preflight.run(_settings(secret_key="short"), db)
    assert not _named(report, "secret key").ok


@requires_db
def test_running_as_the_owner_blocks_production(db):
    """Row-level security is FORCED, so this deployment still isolates. The
    check is about the second lock: the role serving requests is the one that
    could drop it."""
    report = preflight.run(_settings(app_database_url=OWNER_URL), db)
    check = _named(report, "application role")
    assert not check.ok and check.fatal
    assert "connects as the owner" in check.detail


@requires_db
def test_dry_run_blocks_production(db):
    """Correct for a rehearsal. Silent for a launch."""
    report = preflight.run(_settings(dry_run=True), db)
    check = _named(report, "dry run")
    assert not check.ok and check.fatal


@requires_db
def test_a_pending_migration_is_reported(db):
    from runtime.db import MIGRATIONS

    with db.admin_tx() as cur:
        cur.execute("select version from schema_migration order by 1 desc limit 1")
        latest = cur.fetchone()["version"]
    with db.admin_tx() as cur:
        cur.execute("delete from schema_migration where version = %s", (latest,))
    try:
        report = preflight.run(_settings(), db)
        check = _named(report, "migrations")
        assert not check.ok
        assert latest in check.detail
    finally:
        with db.admin_tx() as cur:
            cur.execute("insert into schema_migration (version) values (%s)", (latest,))
    assert MIGRATIONS.is_dir()


@requires_db
def test_an_unreachable_database_stops_the_report_there(db):
    """Nothing below the connection check can be answered without one, and a
    cascade of twelve failures hides the one that matters."""
    from runtime.db import Database

    broken = Database("postgresql://nobody:nobody@127.0.0.1:1/nothing")
    report = preflight.run(_settings(), broken)
    assert not _named(report, "database").ok
    assert [c.name for c in report.checks] == ["secret key", "database"]


def test_a_pooled_endpoint_is_recognised():
    """Neon's console offers the pooled string first, and psycopg names a
    prepared statement after the fifth execution."""
    from runtime.db import _connect_kwargs, is_pooled

    pooled = "postgresql://u:p@ep-cool-a1b2-pooler.eu-central-1.aws.neon.tech/db"
    direct = "postgresql://u:p@ep-cool-a1b2.eu-central-1.aws.neon.tech/db"

    assert is_pooled(pooled)
    assert is_pooled("postgresql://u:p@host/db?pgbouncer=true")
    assert is_pooled("postgresql://u:p@db.example.supabase.co:6543/postgres")
    assert not is_pooled(direct)

    assert _connect_kwargs(pooled)["prepare_threshold"] is None
    assert "prepare_threshold" not in _connect_kwargs(direct)
    # The search_path pin survives either way; it is what stops a role named
    # after a schema from shadowing public.
    assert "search_path" in _connect_kwargs(pooled)["options"]


def test_the_rendering_names_what_is_blocking():
    report = preflight.Report()
    report.add("secret key", False, "published value", fatal=True)
    report.add("agents", True, "disabled")
    rendered = preflight.render(report)
    assert "FAIL  secret key" in rendered
    assert "NOT READY: 1 blocking check" in rendered

    fine = preflight.Report()
    fine.add("agents", True, "disabled")
    assert preflight.render(fine).strip().endswith("READY")


@requires_db
def test_a_remote_database_without_tls_blocks_production(db):
    """The local case is fine and the remote case is not, so the check has to
    know the difference or it is either noise or a false pass."""
    with _unencrypted_db() as unencrypted:
        report = preflight.run(
            _settings(database_url="postgresql://u:p@ep-x.eu-central-1.aws.neon.tech/zolts"),
            unencrypted)
    check = _named(report, "encryption in transit")
    assert not check.ok and check.fatal
    assert "not local" in check.detail


@requires_db
def test_a_local_database_without_tls_is_fine(db):
    with _unencrypted_db() as unencrypted:
        report = preflight.run(_settings(), unencrypted)
    check = _named(report, "encryption in transit")
    assert check.ok
    assert "local" in check.detail


def test_the_isolation_probe_reports_a_real_failure():
    """The probe passes when an unscoped query raises. An earlier version
    called a method psycopg's transaction object does not have and let an outer
    catch swallow the AttributeError, so it passed for the wrong reason. This
    asserts it can still say no."""
    import contextlib

    class Permissive:
        """A database whose unscoped query happily returns a row."""

        @contextlib.contextmanager
        def _connection(self):
            yield self

        @property
        def pool(self):
            return type("Pool", (), {"connection": self._connection})()

        @contextlib.contextmanager
        def transaction(self):
            yield self

        def execute(self, *_args, **_kwargs):
            return type("Result", (), {"fetchone": staticmethod(lambda: (1,))})()

    report = preflight.Report()
    preflight._isolation(report, Permissive())
    check = _named(report, "unscoped reads refused")
    assert not check.ok
    assert "reads across tenants" in check.detail
