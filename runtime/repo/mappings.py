"""Tenant-authored CRM mappings."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from runtime.db import one, rows


def spec_hash(document: dict[str, Any]) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def publish(cur, tenant_id: str, document: dict[str, Any], *,
            reads_opt_out: bool, created_by: str | None = None) -> dict[str, Any]:
    metadata = document["metadata"]
    cur.execute(
        "insert into crm_mapping (tenant_id, provider, name, document, reads_opt_out,"
        " spec_hash, created_by) values (%s,%s,%s,%s,%s,%s,%s)"
        " on conflict (tenant_id, provider) do update set"
        "   name = excluded.name, document = excluded.document,"
        "   reads_opt_out = excluded.reads_opt_out, spec_hash = excluded.spec_hash,"
        "   active = true, updated_at = now()"
        " returning *",
        (tenant_id, metadata["provider"], metadata["name"], json.dumps(document),
         reads_opt_out, spec_hash(document), created_by))
    return one(cur)


def get(cur, provider: str) -> dict[str, Any] | None:
    cur.execute("select * from crm_mapping where provider = %s and active", (provider,))
    return one(cur)


def listing(cur) -> list[dict[str, Any]]:
    cur.execute("select * from crm_mapping order by provider")
    return rows(cur)
