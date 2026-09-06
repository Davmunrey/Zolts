"""Connector contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class TransientError(RuntimeError):
    """A failure worth retrying: timeout, 5xx, rate limit."""


class PermanentError(RuntimeError):
    """A failure that retrying cannot fix: bad credentials, invalid recipient."""


@dataclass(frozen=True)
class Result:
    ok: bool
    provider_ref: str | None = None
    cost_micros: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Request:
    tenant_id: str
    idempotency_key: str
    channel: str
    step: dict[str, Any]
    entity: dict[str, Any]
    contact: dict[str, Any] | None
    secret: str | None
    config: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False


@runtime_checkable
class Connector(Protocol):
    provider: str
    channels: frozenset[str]

    def execute(self, request: Request) -> Result: ...
