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
APP_URL = os.environ.get("ZOLTS_TEST_APP_DATABASE_URL") or OWNER_URL
SECRET = "test-secret-key"

requires_db = pytest.mark.skipif(not OWNER_URL, reason="ZOLTS_TEST_DATABASE_URL is not set")


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
