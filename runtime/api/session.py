"""A browser session for the operator surface.

`/console` authenticated by the `x-api-key` header, which a browser cannot send
when somebody types the URL. The operator surface returned 401 to an operator,
and the quickstart's own instruction was to `curl` it — which renders the page
and can click nothing on it.

Three rules this module exists to keep:

1. **The cookie is not the API key.** A key is a long-lived bearer credential
   shown once; putting it in a cookie puts it in browser storage, in history,
   and on every request to this origin forever. The session token is separate,
   short-lived and revocable.
2. **Revoking a key ends its sessions.** Otherwise revocation stops the
   credential and leaves the browser holding a working door.
3. **A cookie is an ambient credential, so writes carry a CSRF token.** The
   cookie is `SameSite=Strict`, which is already strong; the double-submit
   token is the second lock, and it costs a header. Requests authenticated by
   the `x-api-key` header are exempt — nothing ambient sent them.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from runtime.crypto import hash_token
from runtime.db import Database, one

COOKIE = "zolts_console"
CSRF_COOKIE = "zolts_csrf"
CSRF_HEADER = "x-zolts-csrf"

# Long enough to work through an onboarding, short enough that a laptop left
# open in a co-working space is not a standing grant.
TTL_HOURS = 12


@dataclass(frozen=True)
class Session:
    token: str
    csrf: str
    session_id: str
    expires_at: datetime


@dataclass(frozen=True)
class Resolved:
    tenant_id: str
    session_id: str
    api_key_id: str
    csrf_hash: str


def open_session(db: Database, tenant_id: str, api_key_id: str, *,
                 ip: str | None = None, ttl_hours: int = TTL_HOURS) -> Session:
    """Exchange a validated API key for a browser session."""
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    with db.tenant_tx(tenant_id) as cur:
        cur.execute(
            "insert into console_session (tenant_id, api_key_id, token_hash, csrf_hash,"
            " expires_at, created_ip) values (%s,%s,%s,%s,%s,%s) returning id",
            (tenant_id, api_key_id, hash_token(token), hash_token(csrf), expires_at, ip))
        session_id = str(one(cur)["id"])
    return Session(token=token, csrf=csrf, session_id=session_id, expires_at=expires_at)


def resolve(db: Database, token: str) -> Resolved | None:
    """Who this cookie belongs to, or nothing.

    Expiry, revocation of the session, revocation of the key that opened it and
    suspension of the tenant are all one query, in the database, so no caller
    can check three of the four.
    """
    with db.admin_tx() as cur:
        cur.execute("select * from zolts_internal.resolve_console_session(%s)",
                    (hash_token(token),))
        row = cur.fetchone()
    if row is None:
        return None
    return Resolved(tenant_id=str(row["tenant_id"]), session_id=str(row["session_id"]),
                    api_key_id=str(row["api_key_id"]), csrf_hash=row["csrf_hash"])


def touch(db: Database, tenant_id: str, session_id: str) -> None:
    with db.tenant_tx(tenant_id) as cur:
        cur.execute("update console_session set last_seen_at = now() where id = %s",
                    (session_id,))


def revoke(db: Database, tenant_id: str, session_id: str) -> None:
    with db.tenant_tx(tenant_id) as cur:
        cur.execute("update console_session set revoked_at = now()"
                    " where id = %s and revoked_at is null", (session_id,))


def csrf_ok(resolved: Resolved, presented: str | None) -> bool:
    """Constant-time double-submit check."""
    if not presented:
        return False
    return secrets.compare_digest(hash_token(presented), resolved.csrf_hash)
