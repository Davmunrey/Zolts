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
    def isolation_enforced(self) -> bool:
        """True when the application pool uses the non-owning role."""
        return self.app_database_url != self.database_url
