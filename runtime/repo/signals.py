"""Signal ingest."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from runtime.db import one, rows


def record(cur, tenant_id: str, *, entity_type: str, entity_id: str, type: str,
           strength: float, half_life_h: int, source: str, legal_basis: str,
           payload: dict[str, Any], observed_at: datetime,
           dedupe_key: str | None = None) -> dict[str, Any] | None:
    """Insert one signal. Returns None when the dedupe key already exists.

    A None return is the normal outcome of a source replaying its feed, not an
    error: the caller treats it as "already known" and does no further work.
    """
    cur.execute(
        "insert into signal (tenant_id, entity_type, entity_id, type, strength,"
        " half_life_h, source, legal_basis, payload, observed_at, dedupe_key)"
        " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " on conflict (tenant_id, dedupe_key) where dedupe_key is not null do nothing"
        " returning *",
        (tenant_id, entity_type, entity_id, type, strength, half_life_h, source,
         legal_basis, json.dumps(payload), observed_at, dedupe_key),
    )
    return one(cur)


def within_window(cur, entity_id: str, types: list[str], since: datetime) -> list[dict[str, Any]]:
    cur.execute(
        "select * from signal where entity_id = %s and type = any(%s) and observed_at >= %s"
        " order by observed_at desc",
        (entity_id, types, since),
    )
    return rows(cur)
