"""API-key authentication.

The token is never stored. What is stored is its SHA-256, and resolution goes
through a SECURITY DEFINER function that returns a tenant id and nothing else,
so a compromised application role cannot enumerate keys.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status

from runtime.crypto import hash_token


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    key_id: str
    scopes: frozenset[str]

    def require(self, scope: str) -> None:
        # An empty scope set is full access for the tenant's own data, which is
        # the shape of a first API key. Narrower keys are opt-in.
        if self.scopes and scope not in self.scopes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"key lacks scope '{scope}'")


def _token_from(authorization: str | None, x_api_key: str | None) -> str:
    if x_api_key:
        return x_api_key
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing API key")


async def principal(request: Request,
                    authorization: str | None = Header(default=None),
                    x_api_key: str | None = Header(default=None)) -> Principal:
    token = _token_from(authorization, x_api_key)
    db = request.app.state.db
    with db.admin_tx() as cur:
        cur.execute("select * from zolts_internal.resolve_api_key(%s)", (hash_token(token),))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")
    with db.tenant_tx(str(row["tenant_id"])) as cur:
        cur.execute("update api_key set last_used_at = now() where id = %s", (row["key_id"],))
    return Principal(str(row["tenant_id"]), str(row["key_id"]),
                     frozenset(row["scopes"] or []))


CurrentPrincipal = Depends(principal)
