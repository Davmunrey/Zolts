"""The release job's smoke, run against the app it will smoke.

`scripts/smoke_deployed.py` is what `deploy-vercel.yml` runs after every
release and every hour after that. It gained three paths in one session — a
release whose schema is behind its code, a deployment that answers
"unconfigured", and the cron's locked route — and nothing executed them
before the job would. That is the shape of D-33 and D-40: a script whose
new branches certify whatever they were written to certify. So it runs
here as a subprocess against a real server on a real port, the way the job
runs it, in both shapes a deployment takes: the served API, and the Vercel
entry point with its scheduled routes.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.conftest import APP_URL, OWNER_URL, requires_db

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "smoke_deployed.py"
SECRET = "cron-secret-for-the-smoke"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait(url: str, seconds: float = 60) -> None:
    """Until the server answers anything at all — a 503 is an answer."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except urllib.error.HTTPError:
            return
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(0.2)
    raise AssertionError(f"{url} did not come up in {seconds}s")


def _database_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("ZOLTS_") and k != "CRON_SECRET"}
    env.update({"PYTHONPATH": str(ROOT), "ZOLTS_DATABASE_URL": OWNER_URL or "",
                "ZOLTS_APP_DATABASE_URL": APP_URL or "", "ZOLTS_SECRET_KEY": "test-secret-key"})
    env.update(extra)
    return env


@contextmanager
def _server(argv: list[str], env: dict[str, str]):
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, *argv, "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait(f"http://127.0.0.1:{port}/health")
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def _served_api(env: dict[str, str]):
    return _server(["-m", "runtime.cli", "serve"], env)


def _vercel_entry(env: dict[str, str]):
    return _server(["-m", "uvicorn", "api.index:app"], env)


def _smoke(base: str, *args: str, env: dict[str, str] | None = None):
    """Run the script the way the job does; return (exit code, report, stderr)."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--url", base, "--allow-insecure", *args],
        cwd=ROOT, capture_output=True, text=True,
        env={**{k: v for k, v in os.environ.items() if k != "CRON_SECRET"}, **(env or {})})
    report = json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}
    return result.returncode, report, result.stderr


# -- the served API ---------------------------------------------------------

@requires_db
def test_a_served_api_passes_and_a_host_without_a_cron_is_a_note(db):
    with _served_api(_database_env()) as base:
        code, report, err = _smoke(base, "--expect-migrations", "runtime/migrations")
    assert code == 0, err
    assert report["health"] == 200
    assert report["pendingMigrations"] == []
    assert report["tick"] == 404, "a served API has no cron route, and that is a note"
    assert "no /api/tick" in err
    assert report["console"] in (200, 401) and report["csp"] is True


@requires_db
def test_strict_fails_on_a_deployment_nothing_has_ticked_and_passes_once_it_has(db):
    """`worker ticking` is the signal that catches a cron that never fired.
    Strict is what the hourly job runs, so it must fail here and clear once
    something has ticked — the smoke is not allowed to be an alibi."""
    from runtime.engine.worker import Worker

    with _served_api(_database_env()) as base:
        code, report, err = _smoke(base, "--strict")
        assert code == 1
        assert report["signals"]["worker ticking"] is False
        assert "liveness signal 'worker ticking' is not ok" in err

        Worker(db, secret_key="k", dry_run=True, name="smoke-worker").heartbeat()
        code, report, err = _smoke(base, "--strict")
    assert code == 0, err
    assert report["draining"] is True


@requires_db
def test_a_release_whose_schema_is_behind_its_code_fails(db, tmp_path):
    """The one failure the release order cannot catch: the code carries a
    migration the database has not applied. `/health` says which."""
    migrations = tmp_path / "migrations"
    shutil.copytree(ROOT / "runtime" / "migrations", migrations)
    (migrations / "999_not_yet_applied.sql").write_text("select 1;\n")
    with _served_api(_database_env()) as base:
        code, report, err = _smoke(base, "--expect-migrations", str(migrations))
    assert code == 1
    assert report["pendingMigrations"] == ["999_not_yet_applied"]
    assert "has not applied: 999_not_yet_applied" in err


# -- the Vercel entry point -------------------------------------------------

@requires_db
def test_the_cron_route_is_locked_to_a_stranger_and_the_secret_runs_a_tick(db):
    with _vercel_entry(_database_env(CRON_SECRET=SECRET)) as base:
        code, report, err = _smoke(base)
        assert code == 0, err
        assert report["tick"] == 401, "the cron route answered a stranger with something other than 401"
        assert "tickRan" not in report, "no secret was given, so no tick should have been asked for"

        code, report, err = _smoke(base, env={"CRON_SECRET": SECRET})
    assert code == 0, err
    assert report["tickRan"] == 200
    assert report["tickPasses"] >= 1


@requires_db
def test_a_deployment_with_no_cron_secret_is_a_note_and_a_strict_failure(db):
    """503 from the cron route means the platform's cron runs nothing. The
    smoke says so on every run, and fails the strict one."""
    with _vercel_entry(_database_env()) as base:
        code, report, err = _smoke(base)
        assert code == 0, err
        assert report["tick"] == 503
        assert "CRON_SECRET is not set" in err
        code, _, err = _smoke(base, "--strict")
    assert code == 1
    assert "CRON_SECRET is not set" in err


def test_an_unconfigured_deployment_is_accepted_only_when_asked():
    """The release before the database secrets exist: the surface is up and
    the runtime answers 503 with the variable it is missing. Accepted when
    the job says so; a failure otherwise, because an unconfigured runtime
    that nobody asked for is a release that lost its configuration."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("ZOLTS_") and k != "CRON_SECRET"}
    env["PYTHONPATH"] = str(ROOT)
    with _vercel_entry(env) as base:
        code, report, err = _smoke(base, "--allow-unconfigured")
        assert code == 0, err
        assert report["unconfigured"] == "ZOLTS_DATABASE_URL is required"
        assert "released and unconfigured" in err

        code, report, err = _smoke(base)
    assert code == 1
    assert "/health answered 503" in err
