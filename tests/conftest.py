"""Shared fixtures.

The path insert makes the repository root importable so `zolts` and `runtime`
resolve without installation.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The runtime tests run against a real Postgres. They are skipped, not faked,
# when one is not configured: an isolation property verified against a stub is
# not verified at all.
OWNER_URL = os.environ.get("ZOLTS_TEST_DATABASE_URL")
# Falls back to the owner so a contributor with one URL still runs the suite.
# That fallback is safe for a test that needs *a* database and a lie for one
# that asserts what the restricted role cannot do — see `requires_app_role`.
_DECLARED_APP_URL = os.environ.get("ZOLTS_TEST_APP_DATABASE_URL")
APP_URL = _DECLARED_APP_URL or OWNER_URL
SECRET = "test-secret-key"

_skip_without_db = pytest.mark.skipif(
    not OWNER_URL, reason="ZOLTS_TEST_DATABASE_URL is not set")

# A test that asserts *the serving role cannot do this* is asserting something
# about a role. With no separate app URL the pool connects as the owner, every
# such assertion fails, and the failure is about the environment rather than
# the product — six of them did, including the row-level-security ones, and a
# suite that fails on correct code teaches the team to press re-run (D-73).
_skip_without_app_role = pytest.mark.skipif(
    not (OWNER_URL and _DECLARED_APP_URL),
    reason="ZOLTS_TEST_APP_DATABASE_URL is not set, so the pool connects as the "
           "owner and a privilege the app role lacks cannot be observed")


def requires_db(test):
    """Skip without a real Postgres. See `pytest_collection_modifyitems`."""
    return _skip_without_db(test)


def requires_app_role(test):
    """Skip unless the app pool is a genuinely restricted role.

    The premise, established rather than assumed: `app_role_is_restricted`
    then asserts the connection really is that role before the verdict, so a
    misconfiguration cannot pass as a proof of isolation.
    """
    return _skip_without_db(_skip_without_app_role(test))


def pytest_collection_modifyitems(items):
    """Mark every test that needs Postgres `db`, however it asks for one.

    Two ways exist to need a database here — the `requires_db` decorator, and
    simply requesting the `db` fixture, which skips on its own — and counting
    only the first understates the set by nearly half. README claimed 302 tests
    ran against Postgres; the real number was 187, and neither was measurable
    because a skipif cannot be selected for. Reading the fixture closure counts
    both, so `pytest -m db` is exact and `pytest -m "not db"` is what a
    contributor without a database runs.
    """
    for item in items:
        if "db" in getattr(item, "fixturenames", ()) or any(
                mark.name == "skipif" and mark.kwargs.get("reason", "").startswith(
                    "ZOLTS_TEST_DATABASE_URL") for mark in item.iter_markers()):
            item.add_marker("db")


@pytest.fixture(scope="session")
def db():
    if not OWNER_URL:
        pytest.skip("ZOLTS_TEST_DATABASE_URL is not set")
    from runtime.db import Database

    database = Database(OWNER_URL, APP_URL)
    database.migrate()
    database.grant_app_role()
    yield database
    database.close()


@pytest.fixture
def app_role_is_restricted(db):
    """Assert the app pool really is a role the owner's grants restrict.

    `requires_app_role` skips when no separate URL is configured; this checks
    the premise rather than trusting the variable, because a URL that points at
    the owner by mistake would otherwise turn every privilege assertion below
    it into a test of nothing (D-73). It is the same rule this repository
    applies to ambient time and ambient configuration: establish the premise,
    assert it, then take the verdict.
    """
    assert db.isolation_enforced, (
        "the app pool and the owner pool are the same role, so a privilege the "
        "app role lacks cannot be observed and nothing below proves isolation")
    with db.pool.connection() as conn:
        serving = conn.execute("select current_user").fetchone()[0]
        conn.rollback()
    with db.admin_pool.connection() as owner_conn:
        owning = owner_conn.execute("select current_user").fetchone()[0]
        owner_conn.rollback()
    assert serving != owning, (
        f"the app pool connects as {serving!r}, which is the owner: a privilege "
        f"this role lacks cannot be observed, so nothing below proves isolation")
    return serving


def _tenant(db, region: str, blueprint_id: str) -> dict:
    from runtime.provision import create_tenant

    return create_tenant(db, slug=f"t-{uuid.uuid4().hex[:10]}", name="Test Tenant",
                         region=region, blueprint_id=blueprint_id)


@pytest.fixture
def tenant(db) -> dict:
    return _tenant(db, "eu", "b2b-saas-sales-led")


@pytest.fixture
def other_tenant(db) -> dict:
    return _tenant(db, "us", "ecommerce-dtc")


@pytest.fixture
def fake():
    from runtime.connectors import registry
    from runtime.connectors.fake import FakeConnector

    registry.clear()
    connector = FakeConnector()
    registry.register(connector)
    yield connector
    registry.clear()


@pytest.fixture(autouse=True)
def _clean_database(request):
    """Truncate between tests.

    The worker claims work across tenants, which is correct in production and
    means one test's queued actions are visible to the next. Isolating by
    tenant in the assertions would hide exactly the cross-tenant behaviour
    worth testing, so the database is emptied instead.
    """
    yield
    if "db" not in request.fixturenames or not OWNER_URL:
        return
    db = request.getfixturevalue("db")
    with db.admin_tx() as cur:
        cur.execute("truncate tenant cascade")
        # Not under `tenant`: a heartbeat is a worker's, not a tenant's, and
        # one test's tick must not make the next test's deployment look alive.
        cur.execute("delete from worker_heartbeat")
        # Packs are not a tenant's, so `truncate tenant cascade` leaves them:
        # a test that publishes one would decide the next test's sends. Emptied
        # and reinstalled rather than filtered by publisher, because publishing
        # the shipped digest again rewrites that row's publisher and a filter
        # would then delete the only pack there is.
        from runtime import policy_packs

        cur.execute("delete from policy_pack")
        policy_packs.install_shipped(cur)
