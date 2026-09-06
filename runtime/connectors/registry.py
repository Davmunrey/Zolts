"""Connector lookup by provider name."""

from __future__ import annotations

from runtime.connectors.base import Connector

_REGISTRY: dict[str, Connector] = {}


def register(connector: Connector) -> Connector:
    _REGISTRY[connector.provider] = connector
    return connector


def get_connector(provider: str) -> Connector:
    if provider not in _REGISTRY:
        raise LookupError(f"no connector registered for '{provider}'")
    return _REGISTRY[provider]


def providers_for(channel: str) -> list[str]:
    return sorted(p for p, c in _REGISTRY.items() if channel in c.channels)


def clear() -> None:
    _REGISTRY.clear()
