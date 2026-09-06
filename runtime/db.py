"""Database access, scoped to a tenant by construction.

The only way to obtain a cursor is through `tenant_tx`, which sets the tenant
for the transaction before yielding. Code that needs to run without a tenant
uses `admin_tx`, which is named to be conspicuous in review.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

MIGRATIONS = Path(__file__).resolve().parent / "migrations"

# Pinned on every connection. The default `"$user", public` resolves an
# identically named schema ahead of `public`, which is how a role named after a
# schema silently creates a shadow copy of the database. Nothing here relies on
# implicit resolution.
# Applied by libpq at connect time rather than by a configure callback, so it
# cannot leave a connection inside a transaction, and it is in force for the
# very first statement.
CONNECT_KWARGS = {"options": "-c search_path=public,pg_temp"}


class Database:
    def __init__(self, owner_url: str, app_url: str | None = None) -> None:
        self._owner_url = owner_url
        self._app_url = app_url or owner_url
        self._pool: ConnectionPool | None = None
        self._admin_pool: ConnectionPool | None = None

    # -- pools -----------------------------------------------------------

    @property
    def pool(self) -> ConnectionPool:
        if self._pool is None:
            self._pool = ConnectionPool(self._app_url, min_size=1, max_size=8,
                                        kwargs=CONNECT_KWARGS, open=True)
        return self._pool

    @property
    def admin_pool(self) -> ConnectionPool:
        if self._admin_pool is None:
            self._admin_pool = ConnectionPool(self._owner_url, min_size=1, max_size=4,
                                              kwargs=CONNECT_KWARGS, open=True)
        return self._admin_pool

    def close(self) -> None:
        for pool in (self._pool, self._admin_pool):
            if pool is not None:
                pool.close()
        self._pool = self._admin_pool = None

    # -- transactions ----------------------------------------------------

    @contextlib.contextmanager
    def tenant_tx(self, tenant_id: str) -> Iterator[psycopg.Cursor]:
        """A transaction bound to one tenant for its whole life.

        `set local` is transaction-scoped, so a pooled connection cannot leak a
        tenant into the next borrower even if the caller forgets to reset it.
        """
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute("select set_config('zolts.tenant_id', %s, true)", (str(tenant_id),))
                    yield cur

    @contextlib.contextmanager
    def admin_tx(self) -> Iterator[psycopg.Cursor]:
        """An unscoped transaction. Provisioning and migrations only."""
        with self.admin_pool.connection() as conn:
            with conn.transaction():
                with conn.cursor(row_factory=dict_row) as cur:
                    yield cur

    # -- migrations ------------------------------------------------------

    def migrate(self) -> list[str]:
        """Apply pending migrations in filename order. Returns what ran.

        Each migration runs in its own explicit transaction on a dedicated
        autocommit connection, together with the row that records it. Nesting
        this inside a pooled connection's implicit transaction produced
        savepoints rather than commits, which left the schema applied and the
        ledger empty: the next run then re-applied everything and failed
        half-way. The ledger row and the DDL commit together or not at all.
        """
        files = sorted(MIGRATIONS.glob("*.sql"))
        if not files:
            raise RuntimeError(f"no migrations found in {MIGRATIONS}")
        applied: list[str] = []
        conn = psycopg.connect(self._owner_url, **CONNECT_KWARGS)
        try:
            conn.execute(
                "create table if not exists schema_migration ("
                " version text primary key, applied_at timestamptz not null default now())"
            )
            conn.commit()
            done = {r[0] for r in conn.execute("select version from schema_migration").fetchall()}
            conn.commit()
            for path in files:
                version = path.stem
                if version in done:
                    continue
                try:
                    conn.execute(path.read_text())
                    conn.execute("insert into schema_migration (version) values (%s)", (version,))
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                applied.append(version)
        finally:
            conn.close()
        return applied

    def grant_app_role(self, app_role: str = "zolts_app") -> None:
        """Re-grant table privileges. Idempotent; run after every migration."""
        with self.admin_pool.connection() as conn:
            conn.execute(f"grant select, insert, update, delete on all tables in schema public to {app_role}")
            conn.execute(f"revoke insert, update, delete on tenant from {app_role}")
            conn.execute(f"revoke all on schema_migration from {app_role}")
            conn.commit()


def one(cur: psycopg.Cursor) -> dict[str, Any] | None:
    row = cur.fetchone()
    return dict(row) if row else None


def rows(cur: psycopg.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]
