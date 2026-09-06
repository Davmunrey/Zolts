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
