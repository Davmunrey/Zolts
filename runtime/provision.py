"""Tenant and credential provisioning.

These are the operations that legitimately run without a tenant context, which
is why they live in one file rather than being scattered across the repositories.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime.crypto import Keyring, new_api_token, seal
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


def list_api_keys(db: Database, tenant_id: str) -> list[dict[str, Any]]:
    """Every key this tenant has, live or revoked. Prefixes only, never tokens."""
    with db.tenant_tx(tenant_id) as cur:
        cur.execute(
            "select id, name, prefix, scopes, last_used_at, revoked_at, created_at"
            " from api_key order by created_at desc")
        return [{**dict(r), "id": str(r["id"])} for r in cur.fetchall()]


def revoke_api_key(db: Database, tenant_id: str, key_id: str) -> bool:
    """Stop a key working. Returns whether anything changed.

    Revoking is idempotent and does not delete: the row stays so `last_used_at`
    still answers "was this key used after it leaked", which is the first
    question anybody asks.
    """
    with db.tenant_tx(tenant_id) as cur:
        cur.execute("update api_key set revoked_at = now()"
                    " where id = %s and revoked_at is null", (key_id,))
        return cur.rowcount == 1


def rotate_api_key(db: Database, tenant_id: str, key_id: str) -> IssuedKey:
    """Issue a replacement and revoke the original, in that order.

    Order matters. Revoking first leaves a window in which the tenant has no
    working key, and a rotation that causes an outage is a rotation nobody
    performs a second time.
    """
    with db.tenant_tx(tenant_id) as cur:
        cur.execute("select name, scopes from api_key where id = %s and revoked_at is null",
                    (key_id,))
        existing = one(cur)
    if existing is None:
        raise KeyError(key_id)

    replacement = issue_api_key(db, tenant_id, existing["name"], list(existing["scopes"] or []))
    revoke_api_key(db, tenant_id, key_id)
    return replacement


def create_webhook_endpoint(db: Database, tenant_id: str, *, provider: str,
                            secret_key: "str | Keyring",
                            secret: str | None = None) -> dict[str, str]:
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
            "insert into webhook_endpoint (tenant_id, provider, token, secret_enc,"
            " secret_key_id) values (%s,%s,%s,%s,%s)"
            " on conflict (tenant_id, provider) do update set"
            "   token = excluded.token, secret_enc = excluded.secret_enc,"
            "   secret_key_id = excluded.secret_key_id,"
            "   active = true returning token",
            (tenant_id, provider, token, seal(signing_secret, secret_key),
             Keyring.of(secret_key).primary_id))
        stored = one(cur)["token"]
    return {"path": f"/webhooks/{stored}", "secret": signing_secret,
            "note": "store the secret now; it is not recoverable"}


def store_connection(
    db: Database,
    tenant_id: str,
    *,
    provider: str,
    secret: str,
    secret_key: "str | Keyring",
    display_name: str = "default",
    config: dict[str, Any] | None = None,
) -> str:
    import json

    with db.tenant_tx(tenant_id) as cur:
        cur.execute(
            "insert into connection (tenant_id, provider, display_name, secret_enc,"
            " secret_key_id, config) values (%s,%s,%s,%s,%s,%s)"
            " on conflict (tenant_id, provider, display_name) do update"
            "   set secret_enc = excluded.secret_enc, config = excluded.config,"
            "       secret_key_id = excluded.secret_key_id,"
            "       status = 'active', last_error = null, updated_at = now()"
            " returning id",
            (tenant_id, provider, display_name, seal(secret, secret_key),
             Keyring.of(secret_key).primary_id, json.dumps(config or {})),
        )
        return str(one(cur)["id"])
