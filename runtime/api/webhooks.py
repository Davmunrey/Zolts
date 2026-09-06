"""Inbound webhook endpoints.

Addressed by an opaque token rather than by tenant id: a provider's
configuration screen holds a URL, and a URL carrying a tenant id invites
enumeration.

Signature verification is default-deny. An endpoint configured without a secret
accepts nothing, because an unauthenticated webhook that records outcomes is a
way for anyone who learns the URL to move a customer's measured lift.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status

from runtime.crypto import open_sealed
from runtime.db import Database
from runtime.engine import inbound

log = logging.getLogger(__name__)


def verify(provider: str, secret: str, body: bytes, headers: dict[str, str],
           *, method: str = "POST", url: str = "") -> bool:
    """Check a provider's signature over the raw body.

    The raw body, not a re-serialisation: JSON round-tripping reorders keys and
    changes spacing, and a signature computed over the result never matches.
    """
    if provider == "hubspot":
        # v3: HMAC-SHA256 over method + uri + body + timestamp, base64.
        import base64

        signature = headers.get("x-hubspot-signature-v3")
        timestamp = headers.get("x-hubspot-request-timestamp")
        if not signature or not timestamp:
            return False
        payload = method + url + body.decode("utf-8", "replace") + timestamp
        expected = base64.b64encode(
            hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()).decode()
        return hmac.compare_digest(expected, signature)

    # The general case, and Smartlead's: hex HMAC-SHA256 over the raw body.
    signature = (headers.get("x-smartlead-signature") or headers.get("x-zolts-signature")
                 or headers.get("x-signature") or "")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.replace("sha256=", ""))


def router(db: Database, secret_key: str) -> APIRouter:
    api = APIRouter()

    @api.post("/webhooks/{token}", status_code=status.HTTP_202_ACCEPTED)
    async def receive(token: str, request: Request) -> dict[str, Any]:
        with db.admin_tx() as cur:
            cur.execute("select * from zolts_internal.resolve_webhook(%s)", (token,))
            endpoint = cur.fetchone()
        if endpoint is None:
            # Same answer for an unknown token as for a revoked one. Anything
            # more specific is an oracle for guessing tokens.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such endpoint")

        body = await request.body()
        headers = {k.lower(): v for k, v in request.headers.items()}
        if endpoint["secret_enc"] is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                "endpoint has no signing secret configured")
        secret = open_sealed(endpoint["secret_enc"], secret_key)
        ok = verify(endpoint["provider"], secret, body, headers,
                    method=request.method, url=str(request.url))
        if not ok:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "signature mismatch")

        try:
            payload = json.loads(body or b"{}")
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "body is not JSON") from exc
        # A provider that batches sends a list; one that does not sends an
        # object. Both are the same work.
        events = payload if isinstance(payload, list) else [payload]

        tenant_id = str(endpoint["tenant_id"])
        applied, duplicates = [], 0

        def _read_reply(tid: str, stored: dict):
            """Classify a reply's text, or return nothing and change nothing.

            Every failure here — agents switched off, no model configured, no
            body in the payload, the spend guard refusing — returns None, and
            `apply` then behaves exactly as it did before the agent layer
            existed. An inbound webhook must not start failing because a model
            is unavailable.
            """
            try:
                from runtime.agents.triage import classify
                from runtime.engine.inbound import REPLY, _reply_text
            except Exception:  # noqa: BLE001 - the effects layer stands alone
                return None
            if str(stored.get("event_type") or "").lower() not in REPLY:
                return None
            text = _reply_text(stored.get("payload") or {})
            if not text:
                return None
            factory = getattr(request.app.state, "triage_factory", None)
            if factory is None:
                return None
            try:
                client, guard, budget = factory(tid)
                return classify(client=client, guard=guard, text=text,
                                consumed_usd=budget.get("consumed_usd"),
                                limit_usd=budget.get("limit_usd"))
            except Exception as exc:  # noqa: BLE001 - a reply must still land
                log.warning("reply triage unavailable: %s", exc)
                return None

        with db.tenant_tx(tenant_id) as cur:
            cur.execute("update webhook_endpoint set last_seen_at = now() where id = %s",
                        (endpoint["endpoint_id"],))
            for item in events:
                if not isinstance(item, dict):
                    continue
                stored = inbound.store(cur, tenant_id, provider=endpoint["provider"],
                                       payload=item, signature_ok=True)
                if stored is None:
                    duplicates += 1
                    continue
                result = inbound.apply(cur, tenant_id, stored,
                                       triage=_read_reply(tenant_id, stored))
                applied.append({"event": result.event_id, "matched": result.matched,
                                "effects": result.effects})
        return {"received": len(events), "applied": applied, "duplicates": duplicates}

    return api
