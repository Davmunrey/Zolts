"""The things that break on the day real customer data arrives.

None of this demos. A key that leaks and cannot be revoked, a worker that died
at 3am while `/health` kept saying "ok", and one client holding every thread on
the only unauthenticated route are the failures that lose a partner quietly.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from runtime.api.app import create_app
from runtime.api.throttle import Throttle, caller_of
from runtime.provision import issue_api_key
from tests.conftest import requires_app_role, requires_db


@pytest.fixture
def client(db):
    return TestClient(create_app(db), raise_server_exceptions=False)


@pytest.fixture
def key(db, tenant):
    return issue_api_key(db, str(tenant["id"]), "test", []).token


def _auth(token: str) -> dict[str, str]:
    return {"x-api-key": token}


# -- key lifecycle -------------------------------------------------------

@requires_db
def test_a_leaked_key_can_be_revoked_and_stops_working(db, client, key, tenant):
    second = issue_api_key(db, str(tenant["id"]), "leaked", [])
    assert client.get("/v1/programs", headers=_auth(second.token)).status_code == 200

    assert client.delete(f"/v1/keys/{second.key_id}",
                         headers=_auth(key)).status_code == 204
    assert client.get("/v1/programs", headers=_auth(second.token)).status_code == 401


@requires_db
def test_revoking_never_deletes_the_row(db, client, key, tenant):
    """`last_used_at` still answers 'was this key used after it leaked', which
    is the first question anybody asks."""
    leaked = issue_api_key(db, str(tenant["id"]), "leaked", [])
    client.get("/v1/programs", headers=_auth(leaked.token))
    client.delete(f"/v1/keys/{leaked.key_id}", headers=_auth(key))

    listed = client.get("/v1/keys", headers=_auth(key)).json()
    row = next(k for k in listed if k["id"] == leaked.key_id)
    assert row["revoked_at"] is not None
    assert row["last_used_at"] is not None


@requires_db
def test_a_key_cannot_revoke_itself(client, key):
    """Revoking the key making the request locks the tenant out of their own
    account, so it is refused with the alternative named."""
    listed = client.get("/v1/keys", headers=_auth(key)).json()
    mine = listed[0]["id"]
    response = client.delete(f"/v1/keys/{mine}", headers=_auth(key))
    assert response.status_code == 409
    assert "rotate it instead" in response.json()["detail"]
    assert client.get("/v1/programs", headers=_auth(key)).status_code == 200


@requires_db
def test_rotation_issues_before_it_revokes(client, key):
    """Revoking first leaves a window with no working key, and a rotation that
    causes an outage is one nobody performs a second time."""
    mine = client.get("/v1/keys", headers=_auth(key)).json()[0]["id"]
    rotated = client.post(f"/v1/keys/{mine}/rotate", headers=_auth(key))
    assert rotated.status_code == 200

    replacement = rotated.json()["token"]
    assert client.get("/v1/programs", headers=_auth(replacement)).status_code == 200
    assert client.get("/v1/programs", headers=_auth(key)).status_code == 401


@requires_db
def test_rotation_keeps_the_name_and_scopes(db, client, key, tenant):
    narrow = issue_api_key(db, str(tenant["id"]), "ingest only", ["write"])
    rotated = client.post(f"/v1/keys/{narrow.key_id}/rotate", headers=_auth(key)).json()

    listed = client.get("/v1/keys", headers=_auth(key)).json()
    row = next(k for k in listed if k["id"] == rotated["id"])
    assert row["name"] == "ingest only"
    assert row["scopes"] == ["write"]


@requires_db
def test_a_token_is_never_listed(client, key):
    for row in client.get("/v1/keys", headers=_auth(key)).json():
        assert "token" not in row
        assert row["prefix"].startswith("zk_")


@requires_app_role
def test_keys_are_tenant_scoped(db, client, key, other_tenant, app_role_is_restricted):
    theirs = issue_api_key(db, str(other_tenant["id"]), "theirs", [])
    assert theirs.key_id not in {k["id"] for k in
                                 client.get("/v1/keys", headers=_auth(key)).json()}
    # Revoking across tenants changes nothing rather than erroring, because an
    # error would confirm the key exists.
    assert client.delete(f"/v1/keys/{theirs.key_id}",
                         headers=_auth(key)).status_code == 204
    assert client.get("/v1/programs", headers=_auth(theirs.token)).status_code == 200


# -- liveness ------------------------------------------------------------

@requires_db
def test_liveness_reports_a_draining_deployment(db, client):
    from runtime.engine.worker import Worker

    # A deployment where something has ticked. Without that it is not
    # draining, however empty the outbox is — see the test below.
    Worker(db, secret_key="k").heartbeat()
    answer = client.get("/health/liveness")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["draining"] is True
    assert {s["name"] for s in body["signals"]} == {
        "outbox draining", "actions completing", "connections healthy",
        "tenants can send", "sending domains", "worker ticking",
        "inbound handled"}


@requires_db
def test_a_deployment_where_nothing_has_ticked_is_not_draining(db, client):
    """The outbox signal cannot fail while nothing is due, so a worker that
    died on a quiet weekend — or a cron that never fired — was reported
    healthy until the first action was due, and half an hour more (D-39).
    An empty outbox and no heartbeat is a deployment nobody is running."""
    from runtime import liveness

    with db.admin_tx() as cur:
        cur.execute("delete from worker_heartbeat")
    ticking = next(s for s in liveness.check(db).signals if s.name == "worker ticking")
    assert not ticking.ok
    assert "no worker has ever ticked" in ticking.detail
    answer = client.get("/health/liveness")
    assert answer.status_code == 503
    assert answer.json()["draining"] is False


@requires_db
def test_a_tick_is_a_heartbeat_and_a_stale_one_is_a_dead_worker(db):
    from runtime import liveness
    from runtime.engine.worker import Worker

    Worker(db, secret_key="k", dry_run=True, name="worker-a").tick()
    ticking = next(s for s in liveness.check(db).signals if s.name == "worker ticking")
    assert ticking.ok, ticking.detail
    assert ticking.value == 1

    with db.admin_tx() as cur:
        cur.execute("update worker_heartbeat set ticked_at = now() - interval '%s minutes'"
                    % (liveness.HEARTBEAT_MINUTES + 5))
    stale = next(s for s in liveness.check(db).signals if s.name == "worker ticking")
    assert not stale.ok
    assert "minutes ago" in stale.detail
    assert stale.value >= liveness.HEARTBEAT_MINUTES

    # Another tick, under another name, and the deployment is alive again.
    Worker(db, secret_key="k", dry_run=True, name="worker-b").tick()
    alive = next(s for s in liveness.check(db).signals if s.name == "worker ticking")
    assert alive.ok
    with db.admin_tx() as cur:
        cur.execute("select name from worker_heartbeat order by name")
        assert [r["name"] for r in cur.fetchall()] == ["worker-a", "worker-b"]


@requires_db
def test_a_heartbeat_that_cannot_be_written_does_not_stop_the_tick(db, monkeypatch):
    """Losing the heartbeat makes liveness say nothing is running, which is
    visible. Losing the outbox because the heartbeat failed would not be."""
    from runtime.engine.worker import Worker

    worker = Worker(db, secret_key="k", dry_run=True, name="worker-c")

    def broken(*_args, **_kwargs):
        raise RuntimeError("heartbeat table is gone")

    monkeypatch.setattr(worker, "active_tenants", lambda: [])
    monkeypatch.setattr(worker, "claim", lambda: [])
    # Only the heartbeat reaches the owner pool now, and it is broken.
    monkeypatch.setattr(worker.db, "admin_tx", broken)
    tick = worker.tick()
    assert any("heartbeat" in e for e in tick.errors)


@requires_db
def test_a_stalled_outbox_is_reported_while_health_still_says_ok(db, client, tenant):
    """The morning after the worker died: the database is reachable, every
    request 200s, and nothing has been sent for six hours."""
    from runtime import liveness

    with db.tenant_tx(str(tenant["id"])) as cur:
        for n in range(3):
            cur.execute(
                "insert into action (tenant_id, kind, idempotency_key, state, run_after)"
                " values (%s, 'email.send', %s, 'pending', now() - interval '6 hours')",
                (str(tenant["id"]), f"stalled-{n}"))

    assert client.get("/health").json()["status"] == "ok"

    report = liveness.check(db)
    stalled = next(s for s in report.signals if s.name == "outbox draining")
    assert not stalled.ok
    assert stalled.value == 3
    assert "no worker is running" in stalled.detail
    answer = client.get("/health/liveness")
    assert answer.json()["draining"] is False
    assert answer.status_code == 503, (
        "`docs/20` says point a monitor at this endpoint, and what a monitor reads "
        "is the status code. Answering 200 with the failure in the body is a check "
        "that cannot fail where it is read")


@requires_db
def test_a_wall_of_dead_actions_is_reported(db, client, tenant):
    from runtime import liveness

    with db.tenant_tx(str(tenant["id"])) as cur:
        for n in range(liveness.DEAD_THRESHOLD):
            cur.execute(
                "insert into action (tenant_id, kind, idempotency_key, state)"
                " values (%s, 'email.send', %s, 'dead')", (str(tenant["id"]), f"dead-{n}"))

    signal = next(s for s in liveness.check(db).signals if s.name == "actions completing")
    assert not signal.ok
    assert "integration that stopped working" in signal.detail


@requires_db
def test_a_tenant_with_programs_and_no_connection_is_reported(db, client, tenant):
    """The shape of an onboarding that stopped halfway: programs activated,
    connector never connected. It sends nothing and reports nothing.

    The status here is 'live', which is what the schema allows. The first
    version of this check asked for 'active', a value no program can hold, so
    the signal could never fire — a check that existed and checked nothing.
    """
    from runtime import liveness

    with db.tenant_tx(str(tenant["id"])) as cur:
        cur.execute(
            "insert into program (tenant_id, key, version, spec, spec_hash, status)"
            " values (%s, 'p', 1, '{}', 'h', 'live')", (str(tenant["id"]),))

    signal = next(s for s in liveness.check(db).signals if s.name == "tenants can send")
    assert not signal.ok
    assert "send nothing" in signal.detail

    # And it clears once they can actually send.
    with db.tenant_tx(str(tenant["id"])) as cur:
        cur.execute(
            "insert into connection (tenant_id, provider, display_name, secret_enc)"
            " values (%s, 'smartlead', 'default', '\\x00'::bytea)", (str(tenant["id"]),))
    cleared = next(s for s in liveness.check(db).signals if s.name == "tenants can send")
    assert cleared.ok


@requires_db
def test_liveness_answers_even_when_the_database_is_gone(db):
    """A monitor that gets a stack trace learns nothing."""
    from runtime.db import Database

    broken = Database("postgresql://nobody:nobody@127.0.0.1:1/nothing")
    client = TestClient(create_app(broken), raise_server_exceptions=False)
    answer = client.get("/health/liveness")
    body = answer.json()
    assert body["draining"] is False
    assert body["signals"][0]["name"] == "database"
    assert answer.status_code == 503, "a database that is gone is not a healthy deployment"


# -- the throttle --------------------------------------------------------

def test_the_throttle_admits_up_to_its_limit_then_refuses():
    throttle = Throttle(limit=3, window_seconds=60)
    assert [throttle.allow("1.2.3.4", now=0) for _ in range(4)] == [True, True, True, False]
    # A different caller is unaffected.
    assert throttle.allow("5.6.7.8", now=0)


def test_the_window_slides():
    throttle = Throttle(limit=2, window_seconds=60)
    assert throttle.allow("a", now=0)
    assert throttle.allow("a", now=1)
    assert not throttle.allow("a", now=2)
    # The first two fall out of the window.
    assert throttle.allow("a", now=62)


def test_retry_after_is_never_zero_while_blocked():
    throttle = Throttle(limit=1, window_seconds=60)
    throttle.allow("a", now=0)
    assert throttle.retry_after("a", now=0) >= 1
    assert throttle.retry_after("unknown-caller") == 0


def test_idle_callers_are_forgotten():
    """A dictionary that only grows is a slower outage than the one prevented."""
    throttle = Throttle(limit=5, window_seconds=1)
    for n in range(50):
        throttle.allow(f"caller-{n}")
    assert throttle.forget(before_seconds=0) == 50
    assert throttle._hits == {}


def test_the_caller_is_taken_from_the_forwarded_header_behind_a_proxy():
    class Req:
        headers = {"x-forwarded-for": "203.0.113.7, 10.0.0.1"}
        client = type("C", (), {"host": "10.0.0.1"})()

    assert caller_of(Req()) == "203.0.113.7"

    class Direct:
        headers: dict[str, str] = {}
        client = type("C", (), {"host": "198.51.100.4"})()

    assert caller_of(Direct()) == "198.51.100.4"

    class Unknown:
        headers: dict[str, str] = {}
        client = None

    assert caller_of(Unknown()) == "unknown"


@requires_db
def test_signup_is_throttled(db, client):
    """The only unauthenticated write path. Guessing a 32-byte single-use token
    is not the threat; volume is."""
    from runtime import onboarding

    client.app.state.signup_throttle.limit = 3
    for _ in range(3):
        assert client.get("/v1/signup/zi_nope").status_code == 404

    limited = client.get("/v1/signup/zi_nope")
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1

    # A real invitation is refused too: the ceiling is on the route, not on
    # whether the caller happens to be legitimate.
    issued = onboarding.mint(db, company_name="Throttled Co")
    assert client.post("/v1/signup", json={"token": issued.token}).status_code == 429


# -- restore -------------------------------------------------------------

@requires_db
def test_a_dump_grants_to_a_role_it_does_not_create(db, tmp_path):
    """Roles are cluster-wide, so `pg_dump` of one database emits the GRANTs and
    not the CREATE ROLE. Restored into a fresh managed project the role does not
    exist, every GRANT fails, and psql without ON_ERROR_STOP exits 0 anyway: the
    restore reports success and the application role has no privileges.

    `scripts/restore.sh` creates the role first for exactly this reason. This
    test asserts the reason still holds.
    """
    import subprocess

    from tests.conftest import OWNER_URL

    dump = tmp_path / "dump.sql"
    result = subprocess.run(["pg_dump", "--dbname", OWNER_URL, "-f", str(dump)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"pg_dump unavailable: {result.stderr[:120]}")

    text = dump.read_text()
    assert "zolts_app" in text, "the dump should carry the grants"
    assert "CREATE ROLE" not in text, (
        "if pg_dump starts emitting CREATE ROLE, restore.sh's first step is "
        "redundant and this reasoning needs revisiting")


@requires_db
def test_granting_to_a_missing_role_fails_but_psql_would_not_notice(db):
    """The half of the failure that makes it silent."""
    import psycopg

    with db.admin_tx() as cur:
        with pytest.raises(psycopg.errors.UndefinedObject):
            cur.execute("grant select on api_key to zolts_definitely_not_a_role")
