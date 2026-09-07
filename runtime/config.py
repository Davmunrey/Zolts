"""Runtime configuration, read once from the environment.

Every value that could weaken a security property fails closed: absent means
the runtime refuses to start rather than falling back to a permissive default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def _keys(name: str) -> tuple[str, ...]:
    """A comma-separated list, empty when unset.

    Separated by commas rather than by a numbered suffix so that adding a key
    is one edit to one variable, and removing one — which is what finishes a
    rotation — is the same edit backwards.
    """
    raw = os.environ.get(name) or ""
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_database_url: str
    secret_key: str
    environment: str
    lease_seconds: int
    worker_batch: int
    dry_run: bool
    agents_enabled: bool
    # Keys that still open a sealed credential but never seal a new one. This
    # is the window a rotation runs in: configured when the new key arrives,
    # emptied once `zolts rotate-key` reports nothing outstanding. Leaving one
    # here for ever is the failure mode — the old key stays live, which is the
    # thing the rotation was for.
    #
    # It defaults to empty, and the default is the point: a deployment that is
    # not mid-rotation should not have to know this field exists.
    previous_secret_keys: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        owner = _require("ZOLTS_DATABASE_URL")
        return cls(
            database_url=owner,
            # The application pool connects as a role that cannot bypass RLS.
            # Defaulting it to the owner URL would silently disable tenant
            # isolation, so it defaults to nothing and the caller must be
            # explicit about running unsafely.
            app_database_url=os.environ.get("ZOLTS_APP_DATABASE_URL") or owner,
            secret_key=_require("ZOLTS_SECRET_KEY"),
            previous_secret_keys=_keys("ZOLTS_PREVIOUS_SECRET_KEYS"),
            environment=os.environ.get("ZOLTS_ENV", "development"),
            lease_seconds=int(os.environ.get("ZOLTS_LEASE_SECONDS", "60")),
            worker_batch=int(os.environ.get("ZOLTS_WORKER_BATCH", "25")),
            # A tenant with no live connector must not silently do nothing that
            # looks like success. Dry run is explicit and recorded on the touch.
            dry_run=os.environ.get("ZOLTS_DRY_RUN", "false").lower() == "true",
            # Off unless asked for. A deployment that has not configured a
            # model should run every non-agent program unchanged rather than
            # fail on start-up.
            agents_enabled=os.environ.get("ZOLTS_AGENTS", "false").lower() == "true",
        )

    @property
    def keyring(self) -> "Keyring":
        """The key that seals, and the keys that still open."""
        from runtime.crypto import Keyring

        return Keyring(primary=self.secret_key, previous=self.previous_secret_keys)

    @property
    def isolation_enforced(self) -> bool:
        """True when the application pool uses the non-owning role."""
        return self.app_database_url != self.database_url
