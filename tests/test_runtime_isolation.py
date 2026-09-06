"""Tenant isolation. The property that a breach of would end the company."""

from __future__ import annotations

import pytest

from tests.conftest import OWNER_URL, SECRET
from runtime.provision import issue_api_key, store_connection


def test_a_tenant_reads_only_its_own_rows(db, tenant, other_tenant):
    for t, name in ((tenant, "Mine"), (other_tenant, "Theirs")):
        with db.tenant_tx(str(t["id"])) as cur:
            cur.execute("insert into account (tenant_id, name) values (%s,%s)", (t["id"], name))

    with db.tenant_tx(str(tenant["id"])) as cur:
        cur.execute("select name from account")
        names = {r["name"] for r in cur.fetchall()}
    assert "Mine" in names and "Theirs" not in names


def test_writing_another_tenants_id_is_rejected(db, tenant, other_tenant):
    with pytest.raises(Exception):
        with db.tenant_tx(str(tenant["id"])) as cur:
            cur.execute("insert into account (tenant_id, name) values (%s,'smuggled')",
                        (other_tenant["id"],))


def test_a_query_without_a_tenant_raises_rather_than_returning_nothing(db):
    """An empty result would be indistinguishable from a correct answer."""
    with pytest.raises(Exception, match="zolts.tenant_id is not set"):
        with db.pool.connection() as conn:
            conn.execute("select count(*) from account").fetchone()


def test_updating_a_row_into_another_tenant_is_rejected(db, tenant, other_tenant):
    with db.tenant_tx(str(tenant["id"])) as cur:
        cur.execute("insert into account (tenant_id, name) values (%s,'Mine') returning id",
                    (tenant["id"],))
        account_id = cur.fetchone()["id"]
    with pytest.raises(Exception):
        with db.tenant_tx(str(tenant["id"])) as cur:
            cur.execute("update account set tenant_id = %s where id = %s",
                        (other_tenant["id"], account_id))


def test_api_key_resolution_returns_only_its_own_tenant(db, tenant, other_tenant):
    from runtime.crypto import hash_token

    issued = issue_api_key(db, str(tenant["id"]), "test", ["ingest"])
    with db.admin_tx() as cur:
        cur.execute("select * from zolts_internal.resolve_api_key(%s)", (hash_token(issued.token),))
        resolved = cur.fetchall()
    assert len(resolved) == 1
    assert str(resolved[0]["tenant_id"]) == str(tenant["id"])


def test_a_revoked_key_resolves_to_nothing(db, tenant):
    from runtime.crypto import hash_token

    issued = issue_api_key(db, str(tenant["id"]), "revoked", [])
    with db.admin_tx() as cur:
        cur.execute("update api_key set revoked_at = now() where id = %s", (issued.key_id,))
        cur.execute("select * from zolts_internal.resolve_api_key(%s)", (hash_token(issued.token),))
        assert cur.fetchall() == []


def test_connector_secrets_are_not_readable_from_the_database(db, tenant):
    store_connection(db, str(tenant["id"]), provider="hubspot",
                     secret="pat-na1-super-secret", secret_key=SECRET)
    with db.tenant_tx(str(tenant["id"])) as cur:
        cur.execute("select secret_enc from connection")
        blob = bytes(cur.fetchone()["secret_enc"])
    assert b"pat-na1-super-secret" not in blob


def test_migrations_are_idempotent(db):
    """A second run must be a no-op, not a second schema.

    Regression. The internal schema was called `zolts`, the database role was
    also called `zolts`, and Postgres resolves `"$user"` ahead of `public`. The
    second migration run therefore created a complete duplicate of every table
    inside the shadowing schema and reported success. Two locks now: the schema
    is named `zolts_internal`, and every connection pins its search_path.
    """
    assert db.migrate() == []
    with db.admin_tx() as cur:
        cur.execute(
            "select n.nspname from pg_class c join pg_namespace n on n.oid = c.relnamespace"
            " where c.relname = 'schema_migration'")
        schemas = [r["nspname"] for r in cur.fetchall()]
    assert schemas == ["public"], f"schema_migration exists in {schemas}"


def test_the_internal_schema_is_not_named_after_a_plausible_role(db):
    with db.admin_tx() as cur:
        cur.execute("select nspname from pg_namespace where nspname = 'zolts'")
        assert cur.fetchone() is None


def test_ci_never_reports_green_having_skipped_these():
    """The runtime tests skip without a database. In CI that is a failure.

    A suite that skips its isolation tests and reports 332 passed is worse than
    one that fails: it says the property holds when nothing checked it.
    """
    import os

    if os.environ.get("CI", "").lower() not in {"true", "1"}:
        pytest.skip("only enforced in CI")
    assert OWNER_URL, "ZOLTS_TEST_DATABASE_URL must be set in CI"


def test_the_application_role_cannot_read_invitations(db):
    """Invitations are operator state, not tenant state. They exist before
    their tenant does and are only ever touched by the owner connection behind
    the signup endpoint, so the role that serves tenant requests has no reason
    to reach them — and a signup token hash is not something to leave lying
    within reach of the request path."""
    import psycopg

    with db.pool.connection() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select count(*) from invitation")
        conn.rollback()
