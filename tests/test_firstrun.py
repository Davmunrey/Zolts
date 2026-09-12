"""First run inside the console.

`quickstart` is a CLI, and the founder could not find where to configure the
product (`docs/28`, OX-11). The five steps are a rule in `zolts.firstrun`,
read from rows, tested here without a database; the read itself is tested
against Postgres below, by inserting the rows and asking.
"""

from __future__ import annotations

from tests.conftest import requires_db
from zolts import firstrun
from zolts.firstrun import STEP_KEYS, guide


def _step(said: dict, key: str) -> dict:
    return next(s for s in said["steps"] if s["key"] == key)


FRESH = dict(blueprint="b2b-saas-sales-led", connections=[],
             program_statuses=["draft"], first_signal=None)


def test_a_fresh_tenant_sees_five_steps_in_order_with_one_done():
    said = guide(**FRESH)
    assert said["show"] is True
    assert [s["key"] for s in said["steps"]] == list(STEP_KEYS)
    assert _step(said, "blueprint")["done"] is True
    assert not _step(said, "connect")["done"] and _step(said, "connect")["status"] == "not connected"
    assert not _step(said, "activate")["done"] and _step(said, "activate")["status"] == "none live"
    assert not _step(said, "signal")["done"]
    assert said["done"] == 1 and said["of"] == 5 and said["next"] == "connect"


def test_the_guide_leaves_when_a_programme_goes_live():
    """The exit criterion of OX-11: a tenant with a live programme does not
    see it, whatever else is still missing."""
    said = guide(**{**FRESH, "program_statuses": ["draft", "live"]})
    assert said["show"] is False
    assert _step(said, "activate")["done"] and _step(said, "review")["done"]
    assert not _step(said, "connect")["done"], "still not connected, and still not shown"


def test_a_connection_in_error_is_not_connected_and_the_error_is_said():
    said = guide(**{**FRESH, "connections": [
        {"provider": "hubspot", "status": "error", "last_error": "invalid_grant"}]})
    step = _step(said, "connect")
    assert step["done"] is False
    assert "hubspot" in step["status"] and "invalid_grant" in step["status"]
    connected = guide(**{**FRESH, "connections": [
        {"provider": "hubspot", "status": firstrun.CONNECTED, "last_error": None}]})
    assert _step(connected, "connect")["done"] and "hubspot connected" in _step(connected, "connect")["status"]


def test_the_first_signal_is_named_with_its_moment():
    said = guide(**{**FRESH, "first_signal": ("funding.round", "2026-09-12T08:30:00+00:00")})
    step = _step(said, "signal")
    assert step["done"] and "funding.round" in step["status"] and "2026-09-12 08:30" in step["status"]


def test_every_undone_step_says_what_to_do_and_a_done_one_does_not():
    said = guide(**FRESH)
    for step in said["steps"]:
        if step["done"]:
            assert step["how"] == "", step["key"]
        else:
            assert len(step["how"]) > 40, step["key"]
        assert step["view"] or step["how"], "a step with nowhere to go and nothing to say"


@requires_db
def test_the_guide_reads_the_connection_rows_and_the_programme_statuses(db, tenant):
    from runtime.api import console

    with db.tenant_tx(tenant["id"]) as cur:
        cur.execute("select count(*) as n from connection")
        assert int(cur.fetchone()["n"]) == 0, "the premise: no connection yet"
        fresh = console.first_run_view(cur, tenant, programs=[{"status": "draft"}])
        assert fresh["show"] is True and _step(fresh, "connect")["status"] == "not connected"
        cur.execute(
            "insert into connection (tenant_id, provider, display_name, secret_enc, status,"
            " last_error) values (%s,'hubspot','default','\\x00'::bytea,'error','invalid_grant')",
            (tenant["id"],))
        errored = console.first_run_view(cur, tenant, programs=[{"status": "draft"}])
        assert not _step(errored, "connect")["done"]
        assert "invalid_grant" in _step(errored, "connect")["status"]
        cur.execute("update connection set status = 'active', last_error = null")
        live = console.first_run_view(cur, tenant, programs=[{"status": "live"}])
    assert _step(live, "connect")["done"] and live["show"] is False
