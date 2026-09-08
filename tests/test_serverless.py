"""The worker as an invocation: the routes a cron calls, and what they refuse.

On Vercel there is no worker process. `vercel.json` schedules `/api/tick` —
daily on the Hobby plan, once a minute under `ZOLTS_VERCEL_PLAN=pro`
(decision 43) — and the function drains the outbox in bounded passes
(ADR-041).
Two things have to be true for that to be a worker rather than a hole:

* the routes run nothing for a caller without the cron's bearer, and answer
  503 rather than running unauthenticated when no bearer is configured;
* an invocation stops before the next one starts, and says whether it
  stopped because the outbox was empty or because its budget ran out.

The pure parts are tested with a fake worker and a fake clock. The routes are
tested against the real app on a real Postgres, because the tick claims under
a lease and the lease is what makes a cut-off invocation safe.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime import serverless
from runtime.config import Settings
from runtime.engine.worker import Tick
from tests.conftest import APP_URL, OWNER_URL, requires_db

ROOT = Path(__file__).resolve().parent.parent
SECRET = "cron-secret-for-tests"


# -- the refusal ----------------------------------------------------------

def test_no_configured_secret_is_503_not_open():
    """The cron would call the URL without a bearer, and so would anybody."""
    status, detail = serverless.refusal(None, None)
    assert status == 503
    assert "CRON_SECRET" in detail
    # A bearer presented to a deployment with no secret is still refused:
    # there is nothing to compare it against, and "anything matches" is open.
    assert serverless.refusal("Bearer whatever", "")[0] == 503


def test_a_missing_or_wrong_bearer_is_401():
    assert serverless.refusal(None, SECRET)[0] == 401
    assert serverless.refusal("Bearer wrong", SECRET)[0] == 401
    assert serverless.refusal(SECRET, SECRET)[0] == 401, "the scheme is part of the header"
    assert serverless.refusal("Bearer " + SECRET[:-1], SECRET)[0] == 401


def test_the_right_bearer_is_admitted():
    assert serverless.refusal(f"Bearer {SECRET}", SECRET) is None


# -- the drain ------------------------------------------------------------

class _Worker:
    """Ticks a scripted sequence and remembers how often it was asked."""

    def __init__(self, ticks: list[Tick]) -> None:
        self.ticks = list(ticks)
        self.asked = 0

    def tick(self) -> Tick:
        self.asked += 1
        return self.ticks.pop(0) if self.ticks else Tick()


class _Clock:
    def __init__(self, step: float) -> None:
        self.now, self.step = 0.0, step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def test_the_drain_stops_when_the_outbox_is_empty():
    worker = _Worker([Tick(planned=2, claimed=2, succeeded=2), Tick()])
    drained = serverless.drain(worker, budget_seconds=50, clock=_Clock(0.1))
    assert worker.asked == 2, "one pass with work, one that found nothing, then stop"
    assert drained.passes == 2
    assert drained.succeeded == 2 and drained.claimed == 2
    assert drained.exhausted is False


def test_the_drain_stops_at_its_budget_and_says_so():
    """A queue deeper than a minute is continued by the next minute, and the
    response says that is what happened rather than reporting it as done."""
    worker = _Worker([Tick(claimed=25, succeeded=25) for _ in range(100)])
    drained = serverless.drain(worker, budget_seconds=50, clock=_Clock(30))
    assert drained.exhausted is True
    assert worker.asked < 100, "the budget is the point; it ticked while the outbox had work"
    assert drained.claimed == 25 * drained.passes


def test_errors_are_summed_across_passes():
    worker = _Worker([Tick(claimed=1, failed=1, errors=["a"]),
                      Tick(claimed=1, dead=1, errors=["b"]), Tick()])
    drained = serverless.drain(worker, budget_seconds=50, clock=_Clock(0.1))
    assert drained.errors == ["a", "b"]
    assert drained.failed == 1 and drained.dead == 1


def _cron_interval_seconds(schedule: str) -> int:
    """How often a five-field schedule fires, for the subset this repo ships.

    This used to read the minute field alone: `60 if "*" else 60 * int(field)`.
    Under a daily schedule that is `60 * int("0")` — zero seconds — so the
    guard below failed on a schedule that is perfectly safe. The dangerous
    half is the other one: `30 3 * * *` would have computed 1800 seconds and
    *passed*, having measured nothing about the real twenty-four-hour gap. A
    parser that accepts a field it cannot interpret and returns a plausible
    number is worse than one that refuses, so this refuses. D-57.
    """
    fields = schedule.split()
    assert len(fields) == 5, f"'{schedule}' is not a five-field cron expression"
    minute, hour, day, month, weekday = fields

    def step(field: str, unit: int, whole: int) -> int | None:
        if field == "*":
            return unit
        if field.startswith("*/"):
            return unit * int(field.removeprefix("*/"))
        if field.isdigit():
            return whole          # fires once per the enclosing period
        return None

    per_minute = step(minute, 60, 3600)
    assert per_minute is not None, f"cannot read the minute field of '{schedule}'"
    if minute == "*" or minute.startswith("*/"):
        return per_minute         # sub-hourly; the hour field cannot widen it

    per_hour = step(hour, 3600, 86400)
    assert per_hour is not None, f"cannot read the hour field of '{schedule}'"
    if hour == "*" or hour.startswith("*/"):
        return per_hour
    assert (day, month, weekday) == ("*", "*", "*"), (
        f"'{schedule}' fires less often than daily; this reader does not cover it")
    return 86400


def test_the_interval_reader_refuses_what_it_cannot_read():
    """The guard below is only as good as this arithmetic."""
    assert _cron_interval_seconds("* * * * *") == 60
    assert _cron_interval_seconds("*/15 * * * *") == 900
    assert _cron_interval_seconds("0 * * * *") == 3600
    assert _cron_interval_seconds("0 */4 * * *") == 14400
    assert _cron_interval_seconds("0 3 * * *") == 86400
    assert _cron_interval_seconds("30 3 * * *") == 86400
    for unreadable in ("0 3 * * 1", "0 3 1 * *", "bad * * * *", "* * * *"):
        with pytest.raises(AssertionError):
            _cron_interval_seconds(unreadable)


def test_the_budget_is_under_the_cron_interval():
    """An invocation must be over before the next one starts, and the
    function's own limit must leave a pass room to finish."""
    config = json.loads((ROOT / "vercel.json").read_text())
    tick = next(c for c in config["crons"] if c["path"] == "/api/tick")
    interval = _cron_interval_seconds(tick["schedule"])
    assert serverless.TICK_BUDGET_SECONDS < interval, (
        f"the tick budget ({serverless.TICK_BUDGET_SECONDS}s) is not under the cron "
        f"interval ({interval}s); invocations would overlap")
    limit = config["functions"]["api/index.py"]["maxDuration"]
    assert limit > serverless.TICK_BUDGET_SECONDS + 60, (
        "maxDuration must cover the budget plus one lease, so a pass that overruns "
        "the budget can still finish rather than be killed mid-transaction")


# -- the routes -----------------------------------------------------------

def _settings() -> Settings:
    return Settings(database_url=OWNER_URL or "", app_database_url=APP_URL or "",
                    secret_key="test-secret-key", environment="test",
                    lease_seconds=60, worker_batch=25, dry_run=True,
                    agents_enabled=False)


@pytest.fixture
def secret() -> dict[str, str | None]:
    return {"value": SECRET}


@pytest.fixture
def scheduled(db, secret):
    from runtime.api.app import create_app

    app = create_app(db, secret_key=_settings().keyring)
    serverless.mount(app, db=db, settings=_settings(), secret=lambda: secret["value"])
    return TestClient(app)


@requires_db
def test_the_routes_refuse_a_stranger(scheduled, secret):
    for path in ("/api/tick", "/api/watch"):
        answer = scheduled.get(path)
        assert answer.status_code == 401, path
        assert answer.json()["ran"] is False
        answer = scheduled.get(path, headers={"authorization": "Bearer wrong"})
        assert answer.status_code == 401, path

    secret["value"] = None
    for path in ("/api/tick", "/api/watch"):
        answer = scheduled.get(path, headers={"authorization": f"Bearer {SECRET}"})
        assert answer.status_code == 503, (
            f"{path} ran with no secret configured; that is a public URL that "
            "drains the outbox and bills tenants")
        assert "CRON_SECRET" in answer.json()["detail"]


@requires_db
def test_the_tick_drains_a_due_action_and_leaves_a_heartbeat(db, scheduled, tenant):
    from runtime import liveness

    with db.tenant_tx(str(tenant["id"])) as cur:
        cur.execute(
            "insert into action (tenant_id, kind, idempotency_key, state, run_after)"
            " values (%s, 'email.send', 'due-now', 'pending', now() - interval '1 minute')",
            (str(tenant["id"]),))

    answer = scheduled.get("/api/tick", headers={"authorization": f"Bearer {SECRET}"})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["ran"] is True
    assert body["claimed"] >= 1, "the due action was not claimed by the tick"
    assert body["passes"] >= 1 and body["exhausted"] is False
    assert isinstance(body["warnings"], list)

    with db.tenant_tx(str(tenant["id"])) as cur:
        cur.execute("select state from action where idempotency_key = 'due-now'")
        assert cur.fetchone()["state"] != "pending", "the tick claimed and did nothing"

    ticking = next(s for s in liveness.check(db).signals if s.name == "worker ticking")
    assert ticking.ok, ticking.detail


@requires_db
def test_the_watch_covers_every_active_tenant(scheduled, tenant, other_tenant):
    answer = scheduled.get("/api/watch", headers={"authorization": f"Bearer {SECRET}"})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["ran"] is True
    assert body["tenants"] >= 2
    assert body["watched"] == body["tenants"], "the budget was not spent; every tenant is watched"
    assert body["exhausted"] is False
    assert {p["tenant"] for p in body["passes"]} >= {str(tenant["id"]), str(other_tenant["id"])}


@requires_db
def test_an_exhausted_watch_is_reported_not_hidden(db, tenant, other_tenant):
    """Two tenants, a clock that spends the budget after the first: the second
    is not watched, and the response says so."""
    watched = serverless.watch_all(db, secret_key=_settings().keyring,
                                   budget_seconds=50, clock=_Clock(30))
    assert watched.tenants >= 2
    assert watched.exhausted is True
    assert len(watched.passes) < watched.tenants


# -- the entry point ------------------------------------------------------

def test_an_unconfigured_deployment_answers_503_everywhere():
    """A function whose import raised would 500 with a stack trace in a log.
    This is the same fact as a status a monitor reads and a sentence that
    names the variable."""
    client = TestClient(serverless.unconfigured("ZOLTS_DATABASE_URL is required"))
    for method, path in (("get", "/health"), ("get", "/console"),
                         ("post", "/v1/signals"), ("get", "/api/tick")):
        answer = getattr(client, method)(path)
        assert answer.status_code == 503, (method, path)
        assert answer.json() == {"status": "unconfigured",
                                 "detail": "ZOLTS_DATABASE_URL is required"}


def test_the_entry_point_without_configuration_is_the_unconfigured_app(monkeypatch):
    for name in ("ZOLTS_DATABASE_URL", "ZOLTS_APP_DATABASE_URL", "ZOLTS_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    app = serverless.application()
    answer = TestClient(app).get("/health")
    assert answer.status_code == 503
    assert answer.json()["status"] == "unconfigured"


@requires_db
def test_the_entry_point_builds_the_real_app_from_the_environment(monkeypatch):
    """What `api/index.py` does, with the environment the platform gives it."""
    monkeypatch.setenv("ZOLTS_DATABASE_URL", OWNER_URL)
    monkeypatch.setenv("ZOLTS_APP_DATABASE_URL", APP_URL)
    monkeypatch.setenv("ZOLTS_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv(serverless.CRON_SECRET_VARIABLE, SECRET)
    app = serverless.application()
    try:
        paths = {getattr(route, "path", None) for route in app.routes}
        assert {"/health", "/health/liveness", "/console", "/api/tick", "/api/watch"} <= paths
        client = TestClient(app)
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/api/tick").status_code == 401
        ran = client.get("/api/tick", headers={"authorization": f"Bearer {SECRET}"})
        assert ran.status_code == 200 and ran.json()["ran"] is True
    finally:
        app.state.db.close()


def test_the_vercel_entry_file_exposes_app():
    """The platform imports `app` from `api/index.py` by name. A rename is a
    deployment that builds and answers 404 on everything."""
    source = (ROOT / "api" / "index.py").read_text()
    assert "app = application()" in source
    assert "from runtime.serverless import application" in source


def test_the_secret_is_read_from_the_variable_vercel_sends(monkeypatch):
    """Vercel sends the bearer only for a variable named exactly CRON_SECRET.
    A `ZOLTS_` rename would be a secret nothing sends."""
    assert serverless.CRON_SECRET_VARIABLE == "CRON_SECRET"
    app = FastAPI()
    monkeypatch.setenv("CRON_SECRET", SECRET)
    serverless.mount(app, db=object(), settings=_settings())
    client = TestClient(app)
    assert client.get("/api/tick").status_code == 401
    monkeypatch.delenv("CRON_SECRET")
    assert client.get("/api/tick").status_code == 503
    assert os.environ.get("CRON_SECRET") is None
