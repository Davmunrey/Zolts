"""Tenant and credential provisioning.

These are the operations that legitimately run without a tenant context, which
is why they live in one file rather than being scattered across the repositories.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime.crypto import new_api_token, seal
from runtime.db import Database, one


@dataclass(frozen=True)
class IssuedKey:
    token: str
    key_id: str
    prefix: str


def create_tenant(
    db: Database,
    *,
    slug: str,
    name: str,
    region: str,
    blueprint_id: str,
    compliance_tier: str = "standard",
) -> dict[str, Any]:
    with db.admin_tx() as cur:
        cur.execute(
            "insert into tenant (slug, name, region, blueprint_id, compliance_tier)"
            " values (%s,%s,%s,%s,%s) returning *",
            (slug, name, region, blueprint_id, compliance_tier),
        )
        return one(cur)


def issue_api_key(db: Database, tenant_id: str, name: str, scopes: list[str]) -> IssuedKey:
    """Issue a token. It is returned once and never recoverable afterwards."""
    token, token_hash, prefix = new_api_token()
    with db.admin_tx() as cur:
        cur.execute(
            "insert into api_key (tenant_id, name, token_hash, prefix, scopes)"
            " values (%s,%s,%s,%s,%s) returning id",
            (tenant_id, name, token_hash, prefix, scopes),
        )
        key_id = one(cur)["id"]
    return IssuedKey(token=token, key_id=str(key_id), prefix=prefix)


def create_webhook_endpoint(db: Database, tenant_id: str, *, provider: str,
                            secret_key: str, secret: str | None = None) -> dict[str, str]:
    """Create an inbound endpoint. Returns the URL path and the signing secret.

    The secret is generated when not supplied and returned once, like an API
    token. The endpoint is addressed by an opaque token rather than by tenant
    id, so the URL a provider stores discloses nothing.
    """
    import secrets as _secrets

    token = _secrets.token_urlsafe(24)
    signing_secret = secret or _secrets.token_urlsafe(32)
    with db.tenant_tx(tenant_id) as cur:
        cur.execute(
            "insert into webhook_endpoint (tenant_id, provider, token, secret_enc)"
            " values (%s,%s,%s,%s)"
            " on conflict (tenant_id, provider) do update set"
            "   token = excluded.token, secret_enc = excluded.secret_enc,"
            "   active = true returning token",
            (tenant_id, provider, token, seal(signing_secret, secret_key)))
        stored = one(cur)["token"]
    return {"path": f"/webhooks/{stored}", "secret": signing_secret,
            "note": "store the secret now; it is not recoverable"}


def store_connection(
    db: Database,
    tenant_id: str,
    *,
    provider: str,
    secret: str,
    secret_key: str,
    display_name: str = "default",
    config: dict[str, Any] | None = None,
) -> str:
    import json

    with db.tenant_tx(tenant_id) as cur:
        cur.execute(
            "insert into connection (tenant_id, provider, display_name, secret_enc, config)"
            " values (%s,%s,%s,%s,%s)"
            " on conflict (tenant_id, provider, display_name) do update"
            "   set secret_enc = excluded.secret_enc, config = excluded.config,"
            "       status = 'active', last_error = null, updated_at = now()"
            " returning id",
            (tenant_id, provider, display_name, seal(secret, secret_key), json.dumps(config or {})),
        )
        return str(one(cur)["id"])
