"""The worker as an invocation, for a host that has no processes.

Fly runs the runtime as two processes from one image, and the worker loops.
Vercel runs functions: nothing loops, nothing survives between invocations,
and a process that sleeps is a process billed for sleeping. So on Vercel the
worker is an endpoint, and the platform's own cron invokes it once a minute.

That works because `Worker.tick()` was already invocation-shaped: one pass
plans what is due, claims a batch under a lease, executes it and returns. The
lease is what makes a cut-off invocation safe — an action still leased when
the function is killed frees itself when the lease expires, and its
idempotency key means the provider deduplicates the retry (ADR-041).

Three rules:

* **Nothing runs without the cron's secret.** Vercel sends `Authorization:
  Bearer $CRON_SECRET` when that variable is set, and an invocation without it
  is refused with 401. A deployment with no secret answers 503 rather than
  running unauthenticated work: a public URL that drains the outbox is a
  denial of service, and one that runs `watch` bills every tenant's credits to
  whoever calls it.
* **An invocation stops before the next one starts.** It drains passes until
  the outbox is empty or a budget under the cron interval runs out, and says
  which. A budget that ran out is not an error; it is a queue deeper than a
  minute, and the next minute continues it.
* **This is not a second worker.** It runs the same `Worker` the CLI runs,
  built from the same settings, so what CI executes with `worker --once` is
  what the cron executes.
"""

from __future__ import annotations

import hmac
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import FastAPI, Request, Response, status

from runtime.config import ConfigError, Settings
from runtime.db import Database, one

# Vercel's name, not ours. The platform sends the header only for a variable
# with exactly this name, so renaming it to fit the `ZOLTS_` convention would
# be a secret nobody sends.
CRON_SECRET_VARIABLE = "CRON_SECRET"

# Under the minute the cron fires on, so an invocation is over before the next
# one begins. A pass that overruns it finishes — the deadline is checked
# between passes, never inside one — and `maxDuration` in `vercel.json` leaves
# room for that.
TICK_BUDGET_SECONDS = 50.0
WATCH_BUDGET_SECONDS = 50.0

Clock = Callable[[], float]


@dataclass
class Drained:
    """What one invocation of the tick did, summed over its passes."""
    passes: int = 0
    planned: int = 0
    claimed: int = 0
    succeeded: int = 0
    cancelled: int = 0
    deferred: int = 0
    failed: int = 0
    dead: int = 0
    errors: list[str] = field(default_factory=list)
    # Stopped because the budget ran out, not because the outbox was empty.
    # The next invocation continues; this is reported so that a queue deeper
    # than the cron interval is visible rather than inferred.
    exhausted: bool = False
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def drain(worker, *, budget_seconds: float = TICK_BUDGET_SECONDS,
          clock: Clock = time.monotonic) -> Drained:
    """Tick until the outbox is empty or the budget is spent."""
    started = clock()
    deadline = started + budget_seconds
    out = Drained()
    while True:
        tick = worker.tick()
        out.passes += 1
        for name in ("planned", "claimed", "succeeded", "cancelled", "deferred",
                     "failed", "dead"):
            setattr(out, name, getattr(out, name) + getattr(tick, name))
        out.errors.extend(tick.errors)
        if not tick.did_work:
            break
        if clock() >= deadline:
            out.exhausted = True
            break
    out.seconds = round(clock() - started, 3)
    return out


@dataclass
class WatchedAll:
    """One pass of the watcher over every active tenant, or as many as fit."""
    tenants: int = 0
    passes: list[dict[str, Any]] = field(default_factory=list)
    exhausted: bool = False
    seconds: float = 0.0

    @property
    def credits(self) -> float:
        return round(sum(float(p.get("creditsBilled") or 0) for p in self.passes), 4)

    def as_dict(self) -> dict[str, Any]:
        return {"tenants": self.tenants, "watched": len(self.passes),
                "creditsBilled": self.credits, "exhausted": self.exhausted,
                "seconds": self.seconds, "passes": self.passes}


def watch_all(db: Database, *, secret_key, budget_seconds: float = WATCH_BUDGET_SECONDS,
              limit: int = 500, clock: Clock = time.monotonic) -> WatchedAll:
    """Look for the signals every live program is waiting on, tenant by tenant.

    Shuffled rather than ordered, because the budget is per invocation: an
    ordered list served the same first tenants every time it ran out, and the
    last tenant was never watched at all.
    """
    from runtime import watch

    started = clock()
    deadline = started + budget_seconds
    out = WatchedAll()
    with db.admin_tx() as cur:
        cur.execute("select id from tenant where status = 'active'")
        tenant_ids = [str(r["id"]) for r in cur.fetchall()]
    out.tenants = len(tenant_ids)
    random.shuffle(tenant_ids)

    for tenant_id in tenant_ids:
        if clock() >= deadline:
            out.exhausted = True
            break
        with db.tenant_tx(tenant_id) as cur:
            cur.execute("select * from tenant where id = %s", (tenant_id,))
            tenant = one(cur)
            if tenant is None:
                continue
            result = watch.once(cur, tenant, secret_key=secret_key, limit=limit)
        out.passes.append({"tenant": tenant_id, **result.as_dict()})
    out.seconds = round(clock() - started, 3)
    return out


def refusal(authorization: str | None, secret: str | None) -> tuple[int, str] | None:
    """Why a scheduled invocation may not run, or None when it may.

    Fail closed in both directions. No secret configured is 503, not "run
    anyway": the cron would call this URL without a bearer, and so would
    anybody else. A wrong or missing bearer is 401.
    """
    if not secret:
        return (status.HTTP_503_SERVICE_UNAVAILABLE,
                f"{CRON_SECRET_VARIABLE} is not set; refusing to run scheduled work "
                "unauthenticated")
    presented = (authorization or "").encode()
    if not hmac.compare_digest(presented, f"Bearer {secret}".encode()):
        return status.HTTP_401_UNAUTHORIZED, "the cron secret is missing or wrong"
    return None


def mount(app: FastAPI, *, db: Database, settings: Settings,
          secret: Callable[[], str | None] | None = None,
          tick_budget: float = TICK_BUDGET_SECONDS,
          watch_budget: float = WATCH_BUDGET_SECONDS,
          clock: Clock = time.monotonic) -> None:
    """Add the two scheduled routes to an app the cron can reach.

    Mounted only where a cron exists. A host that runs the worker as a process
    does not get them: two drains of the same outbox are safe, because of the
    leases, and still nothing anybody meant.
    """
    from runtime.engine.worker import from_settings

    read_secret = secret or (lambda: os.environ.get(CRON_SECRET_VARIABLE))
    # One worker per instance, so its name is stable across the invocations
    # this instance serves and the heartbeat reads as one worker ticking.
    worker, warnings = from_settings(db, settings)

    @app.get("/api/tick")
    def tick(request: Request, response: Response) -> dict[str, Any]:
        """One bounded drain of the outbox. GET, because that is what the cron sends."""
        refused = refusal(request.headers.get("authorization"), read_secret())
        if refused:
            response.status_code, detail = refused
            return {"ran": False, "detail": detail}
        drained = drain(worker, budget_seconds=tick_budget, clock=clock)
        return {"ran": True, **drained.as_dict(), "warnings": warnings}

    @app.get("/api/watch")
    def watch(request: Request, response: Response) -> dict[str, Any]:
        """One bounded pass of the signal watcher over every active tenant."""
        refused = refusal(request.headers.get("authorization"), read_secret())
        if refused:
            response.status_code, detail = refused
            return {"ran": False, "detail": detail}
        watched = watch_all(db, secret_key=settings.keyring, budget_seconds=watch_budget,
                            clock=clock)
        return {"ran": True, **watched.as_dict()}


def unconfigured(reason: str) -> FastAPI:
    """An app that says why it cannot serve, on every path.

    A function whose import raises answers 500 with a stack trace in a log
    nobody is reading. This answers 503 with the variable that is missing,
    which is the status a monitor reads and the sentence an operator needs.
    """
    app = FastAPI(title="Zolts", description="This deployment is not configured.")

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def refuse(path: str, response: Response) -> dict[str, str]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unconfigured", "detail": reason}

    return app


def application() -> FastAPI:
    """The ASGI app Vercel imports: the API, plus the two scheduled routes."""
    from runtime.api.app import create_app

    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        return unconfigured(str(exc))
    db = Database(settings.database_url, settings.app_database_url)
    app = create_app(db, secret_key=settings.keyring)
    mount(app, db=db, settings=settings)
    return app
