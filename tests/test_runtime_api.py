"""The HTTP surface, exercised against a real database.

The point of these tests is that there is no route that takes a tenant id. The
only way to name a tenant is to hold one of its keys, so a key from tenant A
presented against tenant B's data returns nothing rather than an error the
caller could probe.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from runtime.api.app import create_app
from runtime.provision import issue_api_key
from tests.conftest import SECRET

SPEC = {
    "trigger": {"events": [{"signal": "funding.round",
                            "where": "payload.amount_usd >= 5000000"}],
                "combine": "any_within", "window": "30d",
                "dedupe": {"key": "account_id", "cooldown": "180d"}},
    "audience": {"sql": "select id as account_id from account"},
    "enrich": {"account": {"require": ["funding_history"], "max_cost_per_account": 0.35}},
    "score": {"model": "pit_r_v3", "weights": {"fit": 0.35, "intent": 0.3,
                                               "timing": 0.25, "reachability": 0.1},
              "floor": 55},
    "route": {"tiers": [{"key": "t2", "when": "score >= 55", "capacity_per_week": 200}]},
    "plays": {"t2": {"template": "agent-assisted", "auto_send": True,
                     "auto_send_requires": {"eval_score": 0.85},
                     "steps": [{"step": "email_1", "channel": "email", "wait": "0d"}]}},
    "policy": {"inherit": "tenant_default"},
    "experiment": {"holdout_pct": 10, "unit": "account", "salt": "api-test",
                   "primary_metric": "opportunity_created_90d"},
    "budget": {"monthly_credits": 40000, "max_cost_per_account": 4.5,
               "on_exceed": "pause_and_alert"},
    "exit": [{"when": "outcome.type == 'unsubscribe'", "reason": "opted_out", "suppress": True}],
}


@pytest.fixture
def client(db):
    return TestClient(create_app(db), raise_server_exceptions=False)


@pytest.fixture
def key(db, tenant):
    return issue_api_key(db, str(tenant["id"]), "test", []).token


def _auth(token: str) -> dict[str, str]:
    return {"x-api-key": token}


# -- authentication ------------------------------------------------------

def test_health_needs_no_key(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and "003_rls" in body["migrations"]


def test_a_request_without_a_key_is_rejected(client):
    assert client.get("/v1/enrollments").status_code == 401


def test_an_invalid_key_is_rejected(client):
    assert client.get("/v1/enrollments", headers=_auth("zk_nope")).status_code == 401


def test_a_key_reaches_only_its_own_tenants_data(client, db, tenant, other_tenant, key):
    other_key = issue_api_key(db, str(other_tenant["id"]), "other", []).token
    made = client.post("/v1/accounts", headers=_auth(key),
                       json={"name": "Mine", "domain": f"{uuid.uuid4().hex[:8]}.com"})
    assert made.status_code == 201

    mine = client.get("/v1/enrollments", headers=_auth(key)).json()
    theirs = client.get("/v1/enrollments", headers=_auth(other_key)).json()
    assert mine == [] and theirs == []

    with db.tenant_tx(str(other_tenant["id"])) as cur:
        cur.execute("select count(*) as n from account")
        assert cur.fetchone()["n"] == 0, "the other tenant must not see the account"


def test_a_scoped_key_cannot_exceed_its_scopes(client, db, tenant):
    read_only = issue_api_key(db, str(tenant["id"]), "read", ["read"]).token
    response = client.post("/v1/accounts", headers=_auth(read_only), json={"name": "X"})
    assert response.status_code == 403


# -- programs ------------------------------------------------------------

def test_publishing_a_program_validates_it(client, key):
    response = client.post("/v1/programs", headers=_auth(key),
                           json={"key": "api-test", "version": "1.0.0", "spec": SPEC,
                                 "name": "API test",
                                 "blueprint": "b2b-saas-sales-led",
                                 "activate": True})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "live" and body["spec_hash"]


def test_a_program_that_would_fail_ci_is_refused(client, key):
    broken = {k: v for k, v in SPEC.items() if k != "route"}
    response = client.post("/v1/programs", headers=_auth(key),
                           json={"key": "broken", "version": "1.0.0", "name": "Broken",
                                 "spec": broken})
    assert response.status_code == 422


def test_a_program_without_a_holdout_is_refused(client, key):
    no_holdout = {k: v for k, v in SPEC.items() if k != "experiment"}
    response = client.post("/v1/programs", headers=_auth(key),
                           json={"key": "no-holdout", "version": "1.0.0",
                                 "name": "No holdout", "spec": no_holdout})
    assert response.status_code == 422


def test_only_one_version_can_be_live(client, key):
    for version in ("1.0.0", "1.1.0"):
        client.post("/v1/programs", headers=_auth(key),
                    json={"key": "versioned", "version": version, "spec": SPEC,
                          "name": "API test", "blueprint": "b2b-saas-sales-led", "activate": True})
    live = [p for p in client.get("/v1/programs", headers=_auth(key)).json()
            if p["key"] == "versioned" and p["status"] == "live"]
    assert len(live) == 1 and live[0]["version"] == "1.1.0"


# -- ingest --------------------------------------------------------------

def _signal(entity_id: str, dedupe: str | None = None) -> dict:
    # Observed now, not at a fixed date: the trigger window is 30 days, and a
    # signal older than the window is correctly ignored. Pinning the date makes
    # the test expire.
    return {"entity_type": "account", "entity_id": entity_id, "type": "funding.round",
            "strength": 0.7, "half_life_h": 720, "source": "api-test",
            "payload": {"stage": "series_a", "amount_usd": 9_000_000},
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "dedupe_key": dedupe}


def test_a_signal_older_than_the_trigger_window_does_not_enroll(client, key):
    """A stale signal is not a fresh one. Enrolling on it would restart a play
    on evidence that has already decayed."""
    client.post("/v1/programs", headers=_auth(key),
                json={"key": "api-test", "version": "1.0.0", "spec": SPEC,
                      "name": "API test", "blueprint": "b2b-saas-sales-led", "activate": True})
    account = client.post("/v1/accounts", headers=_auth(key),
                          json={"name": "Acme", "domain": f"{uuid.uuid4().hex[:8]}.com"}).json()
    stale = _signal(account["id"], "stale-1")
    stale["observed_at"] = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    body = client.post("/v1/signals", headers=_auth(key), json=stale).json()
    assert body["accepted"] and body["enrollments"] == []


def test_a_signal_enrolls_through_the_api(client, key):
    client.post("/v1/programs", headers=_auth(key),
                json={"key": "api-test", "version": "1.0.0", "spec": SPEC,
                      "name": "API test", "blueprint": "b2b-saas-sales-led", "activate": True})
    account = client.post("/v1/accounts", headers=_auth(key),
                          json={"name": "Acme", "domain": f"{uuid.uuid4().hex[:8]}.com",
                                "country": "ES"}).json()
    response = client.post("/v1/signals", headers=_auth(key),
                           json=_signal(account["id"], "d-1"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] and len(body["enrollments"]) == 1
    assert body["enrollments"][0]["tier"] == "t2"


def test_a_replayed_signal_reports_itself_as_deduplicated(client, key):
    client.post("/v1/programs", headers=_auth(key),
                json={"key": "api-test", "version": "1.0.0", "spec": SPEC,
                      "name": "API test", "blueprint": "b2b-saas-sales-led", "activate": True})
    account = client.post("/v1/accounts", headers=_auth(key),
                          json={"name": "Acme", "domain": f"{uuid.uuid4().hex[:8]}.com"}).json()
    payload = _signal(account["id"], "replay-key")
    client.post("/v1/signals", headers=_auth(key), json=payload)
    second = client.post("/v1/signals", headers=_auth(key), json=payload).json()
    assert second["deduplicated"] is True and second["enrollments"] == []


def test_ingest_without_a_dedupe_key_warns(client, key):
    """Silence here would let a replaying source inflate every score it touches."""
    account = client.post("/v1/accounts", headers=_auth(key),
                          json={"name": "Acme", "domain": f"{uuid.uuid4().hex[:8]}.com"}).json()
    body = client.post("/v1/signals", headers=_auth(key),
                       json=_signal(account["id"])).json()
    assert any("dedupe_key" in w for w in body["warnings"])


# -- measurement ---------------------------------------------------------

def test_measurement_reports_the_effect_the_sample_can_detect(client, db, tenant, key):
    published = client.post("/v1/programs", headers=_auth(key),
                            json={"key": "api-test", "version": "1.0.0", "spec": SPEC,
                                  "name": "API test", "blueprint": "b2b-saas-sales-led",
                                  "activate": True}).json()
    body = client.get(f"/v1/programs/{published['id']}/measurement",
                      headers=_auth(key)).json()
    assert body["treatment"] == 0 and body["control"] == 0
    assert body["significant"] is False, "no data can never be a significant result"


# -- the console ---------------------------------------------------------

def test_the_console_is_served_with_live_data(client, key):
    published = client.post("/v1/programs", headers=_auth(key),
                            json={"key": "api-test", "version": "1.0.0", "spec": SPEC,
                                  "name": "API test", "blueprint": "b2b-saas-sales-led",
                                  "activate": True})
    assert published.status_code == 201, published.text
    response = client.get("/console", headers=_auth(key))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "api-test" in response.text
    assert "/*__FIXTURE__*/" not in response.text, "the placeholder must be replaced"


def test_the_console_policy_is_derived_from_what_is_served(client, key):
    response = client.get("/console", headers=_auth(key))
    policy = response.headers["content-security-policy"]
    assert "'sha256-" in policy
    assert "style-src-attr 'unsafe-inline'" in policy, (
        "a hash covers a <style> element but never a style='' attribute; "
        "locking both refuses every runtime style")
    assert "script-src 'self' 'sha256-" in policy
    assert "'unsafe-inline'" not in policy.split("script-src")[1].split(";")[0]


def test_a_tenants_live_figures_are_never_cached(client, key):
    response = client.get("/console", headers=_auth(key))
    assert "no-store" in response.headers["cache-control"]


def test_the_console_needs_a_key(client):
    assert client.get("/console").status_code == 401


def test_the_console_reports_no_lift_it_cannot_resolve(client, key):
    """With no outcomes, nothing may be reported as significant."""
    client.post("/v1/programs", headers=_auth(key),
                json={"key": "api-test", "version": "1.0.0", "spec": SPEC,
                      "name": "API test", "blueprint": "b2b-saas-sales-led", "activate": True})
    data = client.get("/v1/console", headers=_auth(key)).json()
    assert data["programs"], "a live program must appear"
    program = data["programs"][0]
    assert program["significant"] is False
    assert program["pipeline"] is None, "pipeline is withheld until the lift clears the MDE"


def test_the_console_shows_only_its_own_tenants_programs(client, db, tenant, other_tenant, key):
    other_key = issue_api_key(db, str(other_tenant["id"]), "other", []).token
    client.post("/v1/programs", headers=_auth(key),
                json={"key": "mine", "version": "1.0.0", "spec": SPEC, "name": "Mine",
                      "blueprint": "b2b-saas-sales-led", "activate": True})
    theirs = client.get("/v1/console", headers=_auth(other_key)).json()
    assert theirs["programs"] == []
    assert theirs["tenant"]["slug"] == other_tenant["slug"]


def test_the_live_view_model_matches_the_fixtures_shape(client, db, tenant, key):
    """One shape means one rendering path.

    The trace rendered `undefined · undefined` the first time the console ran
    against live data, because the live decisions carried different keys from
    the fixture the surface was built against.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from build_fixture import build as build_fixture

    fixture = build_fixture()
    client.post("/v1/programs", headers=_auth(key),
                json={"key": "api-test", "version": "1.0.0", "spec": SPEC, "name": "API test",
                      "blueprint": "b2b-saas-sales-led", "activate": True})
    live = client.get("/v1/console", headers=_auth(key)).json()

    missing = set(fixture) - set(live)
    assert not missing, f"the live view model omits {sorted(missing)}"

    program_keys = set(fixture["programs"][0])
    assert program_keys <= set(live["programs"][0]), (
        f"program view omits {sorted(program_keys - set(live['programs'][0]))}")

    # A fresh tenant has no decisions, and a check that only runs when data
    # happens to exist is the check that was missing when this broke.
    from runtime.repo import entities, ledger

    with db.tenant_tx(str(tenant["id"])) as cur:
        person = entities.upsert_person(cur, str(tenant["id"]), email="trace@example.com",
                                        full_name="Trace Subject", country="ES")
        ledger.record_decision(cur, str(tenant["id"]), subject_type="person",
                               subject_id=str(person["id"]), action="email.send",
                               decision="deny", rule_key="suppression.unsubscribed",
                               jurisdiction="ES", rationale="contact opted out of email")
    live = client.get("/v1/console", headers=_auth(key)).json()

    decision_keys = set(next(iter(fixture["decisions"].values()))[0])
    sample = next(iter(live["decisions"].values()))
    assert decision_keys <= set(sample[0]), (
        f"decision view omits {sorted(decision_keys - set(sample[0]))}")
    assert sample[0]["account"] == "Trace Subject", "the trace names what an operator recognises"
    assert sample[0]["channel"] == "email"


def test_the_live_view_model_honours_the_rendering_contract(client, key):
    """Same contract as the fixture, on the model the API actually serves.

    The surface branches on `treat` and then reads `absLift` and `mde`. A
    program with no outcomes carried a rate of 0.00 and a null lift, so the
    guard passed and the next line called toFixed on null.
    """
    client.post("/v1/programs", headers=_auth(key),
                json={"key": "api-test", "version": "1.0.0", "spec": SPEC, "name": "API test",
                      "blueprint": "b2b-saas-sales-led", "activate": True})
    for program in client.get("/v1/console", headers=_auth(key)).json()["programs"]:
        if program["treat"] is None:
            assert program["ctrl"] is None and program["absLift"] is None
            assert program["mde"] is None and program["pipeline"] is None
            assert program["significant"] is False
            assert program["unresolvedReason"], "the surface must be able to say why"
        else:
            assert program["ctrl"] is not None and program["absLift"] is not None
        if program["significant"]:
            assert program["mde"] is not None and program["pipeline"]


def test_a_published_program_keeps_its_blueprint(client, key):
    """The linter scopes rules by blueprint, and the console has a column for it."""
    client.post("/v1/programs", headers=_auth(key),
                json={"key": "api-test", "version": "1.0.0", "spec": SPEC, "name": "API test",
                      "blueprint": "b2b-saas-sales-led", "activate": True})
    data = client.get("/v1/console", headers=_auth(key)).json()
    assert data["programs"][0]["blueprint"] == "b2b-saas-sales-led"


# -- first run -----------------------------------------------------------

def test_quickstart_leaves_a_tenant_that_can_be_used_immediately(db):
    """A first run is one command, and what it produces must actually work."""
    import uuid as _uuid

    from runtime.cli import quickstart

    slug = f"qs-{_uuid.uuid4().hex[:8]}"
    result = quickstart(db, secret_key=SECRET, slug=slug, name="Quickstart Co",
                        region="eu", blueprint="b2b-saas-sales-led",
                        base_url="http://testserver")
    assert len(result["programs"]) == 4
    assert all(p["lint"] == [] for p in result["programs"]), "shipped examples must lint clean"

    client = TestClient(create_app(db), raise_server_exceptions=False)
    listed = client.get("/v1/programs", headers=_auth(result["api_key"])).json()
    assert {p["status"] for p in listed} == {"live"}
    assert client.get("/console", headers=_auth(result["api_key"])).status_code == 200


def test_quickstart_does_not_connect_a_provider_for_you(db):
    """A tenant with no connection fails loudly on dispatch, which is the
    correct first experience: the operator says which provider is theirs rather
    than discovering later that nothing was ever sent."""
    import uuid as _uuid

    from runtime.cli import quickstart

    result = quickstart(db, secret_key=SECRET, slug=f"qs-{_uuid.uuid4().hex[:8]}",
                        name="Quickstart Co", region="eu",
                        blueprint="b2b-saas-sales-led", base_url="http://testserver")
    with db.tenant_tx(result["tenant"]["id"]) as cur:
        cur.execute("select count(*) as n from connection")
        assert cur.fetchone()["n"] == 0
