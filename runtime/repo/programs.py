"""Program versions."""

from __future__ import annotations

import json
from typing import Any

from runtime.db import one, rows


def publish(cur, tenant_id: str, *, key: str, version: str, spec: dict[str, Any],
            spec_hash: str, status: str = "draft", created_by: str | None = None) -> dict[str, Any]:
    cur.execute(
        "insert into program (tenant_id, key, version, spec, spec_hash, status, created_by)"
        " values (%s,%s,%s,%s,%s,%s,%s)"
        " on conflict (tenant_id, key, version) do update set"
        "   spec = excluded.spec, spec_hash = excluded.spec_hash"
        " returning *",
        (tenant_id, key, version, json.dumps(spec), spec_hash, status, created_by),
    )
    return one(cur)


def activate(cur, program_id: str) -> dict[str, Any]:
    """Make one version live, retiring whichever version currently is.

    Both statements are in the caller's transaction, so the partial unique index
    on (tenant_id, key) where status='live' is never violated even under a
    concurrent publish: the loser blocks and then finds the row already moved.
    """
    cur.execute("select tenant_id, key from program where id = %s", (program_id,))
    row = one(cur)
    if row is None:
        raise LookupError(f"program {program_id} not found in this tenant")
    cur.execute(
        "update program set status = 'paused' where key = %s and status = 'live' and id <> %s",
        (row["key"], program_id),
    )
    cur.execute("update program set status = 'live' where id = %s returning *", (program_id,))
    return one(cur)


def live(cur) -> list[dict[str, Any]]:
    cur.execute("select * from program where status = 'live' order by key")
    return rows(cur)


def get(cur, program_id: str) -> dict[str, Any] | None:
    cur.execute("select * from program where id = %s", (program_id,))
    return one(cur)
