"""Program versions."""

from __future__ import annotations

import json
from typing import Any

from runtime.db import one, rows


class VersionIsImmutable(ValueError):
    """A published version was republished with different content."""


def publish(cur, tenant_id: str, *, key: str, version: str, spec: dict[str, Any],
            spec_hash: str, status: str = "draft", created_by: str | None = None,
            metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Store a version. A version already written is never rewritten.

    `metadata` carries the document's name, blueprint and owner. The blueprint
    is not decoration: the linter scopes rules by it and the overlay resolver
    needs it to know which policy the program inherits.

    This used to be a plain upsert: `do update set spec = excluded.spec`. So
    publishing v2.1.0 twice with different content replaced the spec of a
    version that was already live, and every enrollment running under it
    pointed at a document that no longer described what it did — including the
    measurement, which would attribute one lift to two different programs.
    Program logic is versioned configuration; a version is therefore immutable.

    Republishing the *same* content still succeeds, because a retried request
    and a re-seeded starter program are both legitimate and neither changes
    anything. Different content under a version that exists is refused.
    """
    cur.execute(
        "insert into program (tenant_id, key, version, spec, spec_hash, status, created_by,"
        " metadata) values (%s,%s,%s,%s,%s,%s,%s,%s)"
        " on conflict (tenant_id, key, version) do update set"
        "   metadata = excluded.metadata"
        "   where program.spec_hash = excluded.spec_hash"
        " returning *",
        (tenant_id, key, version, json.dumps(spec), spec_hash, status, created_by,
         json.dumps(metadata or {})),
    )
    row = one(cur)
    if row is None:
        # `do update ... where` that matches nothing returns no row, which is
        # how a conflicting republish arrives here rather than as an error.
        raise VersionIsImmutable(
            f"{key} v{version} already exists with different content. "
            "Publish a new version — the one running keeps its spec.")
    return row


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
