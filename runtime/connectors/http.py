"""Shared HTTP behaviour for provider connectors.

Every provider call goes through here so that the classification of failures is
one decision made once. Getting it wrong in either direction is expensive: a
permanent error treated as transient burns the retry budget and the provider's
rate limit, and a transient error treated as permanent silently drops a send.
"""

from __future__ import annotations

from typing import Any

import httpx

from runtime.connectors.base import PermanentError, TransientError

TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# 409 is absent deliberately: a conflict on an idempotent create means the
# resource already exists, which the caller handles as success rather than as
# an error of either kind.
_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}


def request(method: str, url: str, *, token: str | None = None,
            json: dict[str, Any] | None = None, params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None, allow: frozenset[int] | None = None,
            client: httpx.Client | None = None) -> httpx.Response:
    """Call a provider and classify the outcome.

    `allow` names statuses the caller will interpret itself. A probe for a
    resource that may legitimately be absent passes {404}; without it the 404
    raises here and the caller's "create it then" branch is unreachable.
    """
    all_headers = {"content-type": "application/json", **(headers or {})}
    if token:
        all_headers["authorization"] = f"Bearer {token}"
    owned = client is None
    client = client or httpx.Client(timeout=TIMEOUT)
    try:
        response = client.request(method, url, json=json, params=params, headers=all_headers)
    except httpx.TimeoutException as exc:
        raise TransientError(f"{method} {url} timed out: {exc}") from exc
    except httpx.TransportError as exc:
        raise TransientError(f"{method} {url} transport failure: {exc}") from exc
    finally:
        if owned:
            client.close()

    if allow and response.status_code in allow:
        return response
    if response.status_code in _TRANSIENT_STATUS:
        raise TransientError(f"{method} {url} -> {response.status_code}: {response.text[:300]}")
    if response.status_code in (401, 403):
        # A credential problem does not improve with retries, and hammering an
        # invalid token is how an account gets locked.
        raise PermanentError(f"{method} {url} -> {response.status_code}: credentials rejected")
    if response.status_code >= 400 and response.status_code != 409:
        raise PermanentError(f"{method} {url} -> {response.status_code}: {response.text[:300]}")
    return response


def body(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {"data": payload}
