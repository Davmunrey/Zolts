"""A CRM this repository has never seen, connected by a document.

The tests below are the claim that a mapping is a real integration and not a
demo: it is validated at the door, stored per tenant, invisible to every other
tenant, and it produces the same canonical records through the same normaliser
as a connector written by hand.
"""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from runtime.api.app import create_app
from runtime.provision import issue_api_key
from tests.conftest import requires_db

PUSH_MAPPING = {
    "apiVersion": "zolts/v1",
    "kind": "CrmMapping",
    "metadata": {"provider": "acme-push", "name": "Acme internal CRM",
                 "owner": "revops@acme.example"},
    "spec": {
        "transport": {"kind": "push"},
        "accounts": {"external_id": "id | str", "name": "legal_name",
                     "domain": "website | domain", "country": "hq.country | upper"},
        "contacts": {"external_id": "id | str",
                     "email": "emails[primary].address | trim | lower",
                     "full_name": "name_parts | join",
                     "account_external_id": "company.id | str",
                     "consent": {"field": "mail_pref",
                                 "values": {"opted_in": "allowed",
                                            "opted_out": "opted_out",
                                            "never_asked": "unknown"},
                                 "default": "unknown"}},
    },
}

BATCH = {
    "accounts": [
        {"id": 41, "legal_name": "Northwind Traders SL",
         "website": "https://www.northwind.example/pricing", "hq": {"country": "es"}},
        {"id": 42, "legal_name": "Contoso GmbH", "website": "contoso.example",
         "hq": {"country": "de"}},
    ],
    "contacts": [
        {"id": 900, "company": {"id": 41}, "mail_pref": "opted_in",
         "name_parts": ["Ada", "Lovelace"],
         "emails": [{"address": "old@northwind.example"},
                    {"address": "  Ada@Northwind.example ", "primary": True}]},
        {"id": 901, "company": {"id": 41}, "mail_pref": "opted_out",
         "name_parts": ["Grace", "Hopper"],
         "emails": [{"address": "grace@northwind.example", "primary": True}]},
        {"id": 902, "company": {"id": 42}, "mail_pref": "never_asked",
         "name_parts": ["Alan", "Turing"],
         "emails": [{"address": "alan@contoso.example", "primary": True}]},
    ],
}


@pytest.fixture
def client(db):
    return TestClient(create_app(db), raise_server_exceptions=False)


@pytest.fixture
def key(db, tenant):
    return issue_api_key(db, str(tenant["id"]), "test", []).token


@pytest.fixture
def other_key(db, other_tenant):
    return issue_api_key(db, str(other_tenant["id"]), "test", []).token


def _auth(token: str) -> dict[str, str]:
    return {"x-api-key": token}


def _without_consent() -> dict:
    document = copy.deepcopy(PUSH_MAPPING)
    document["metadata"]["provider"] = "acme-blind"
    document["spec"]["contacts"].pop("consent")
    return document


# -- publishing ----------------------------------------------------------

@requires_db
def test_publish_stores_the_mapping_and_derives_opt_out(client, key):
    response = client.post("/v1/crm/mappings", json=PUSH_MAPPING, headers=_auth(key))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["provider"] == "acme-push"
    assert body["reads_opt_out"] is True
    assert body["warning"] is None
    assert len(body["spec_hash"]) == 16


@requires_db
def test_a_mapping_with_no_consent_field_is_published_with_a_warning(client, key):
    """Not refused. Said out loud, once, where an operator reads it."""
    response = client.post("/v1/crm/mappings", json=_without_consent(), headers=_auth(key))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["reads_opt_out"] is False
    assert "consent unknown" in body["warning"]


@requires_db
def test_an_invalid_mapping_is_refused_with_a_readable_reason(client, key):
    broken = copy.deepcopy(PUSH_MAPPING)
    broken["spec"]["contacts"]["email"] = "emails[primary].address | shout"
    response = client.post("/v1/crm/mappings", json=broken, headers=_auth(key))
    assert response.status_code == 422
    assert "unknown transform 'shout'" in response.json()["detail"]


@requires_db
def test_consent_defaulting_to_allowed_with_no_value_map_is_refused(client, key):
    """The shape that marks everyone contactable has to be a sentence
    somebody wrote, not one they fell into."""
    reckless = copy.deepcopy(PUSH_MAPPING)
    reckless["spec"]["contacts"]["consent"] = {"field": "mail_pref", "default": "allowed"}
    response = client.post("/v1/crm/mappings", json=reckless, headers=_auth(key))
    assert response.status_code == 422
    assert "every contact contactable" in response.json()["detail"]


@requires_db
def test_republishing_a_provider_replaces_it_in_place(client, key):
    first = client.post("/v1/crm/mappings", json=PUSH_MAPPING, headers=_auth(key)).json()

    changed = copy.deepcopy(PUSH_MAPPING)
    changed["spec"]["contacts"]["title"] = "role"
    second = client.post("/v1/crm/mappings", json=changed, headers=_auth(key)).json()

    assert second["id"] == first["id"]
    assert second["spec_hash"] != first["spec_hash"]
    listing = client.get("/v1/crm/mappings", headers=_auth(key)).json()
    assert [m["provider"] for m in listing] == ["acme-push"]


@requires_db
def test_a_mapping_belongs_to_one_tenant(client, key, other_key):
    client.post("/v1/crm/mappings", json=PUSH_MAPPING, headers=_auth(key))

    assert client.get("/v1/crm/mappings", headers=_auth(other_key)).json() == []
    pushed = client.post("/v1/crm/acme-push/records", json=BATCH, headers=_auth(other_key))
    assert pushed.status_code == 404


# -- pushing records -----------------------------------------------------

@requires_db
def test_pushed_records_land_as_canonical_entities(db, client, key, tenant):
    client.post("/v1/crm/mappings", json=PUSH_MAPPING, headers=_auth(key))
    response = client.post("/v1/crm/acme-push/records", json=BATCH, headers=_auth(key))
    assert response.status_code == 200, response.text

    report = response.json()
    assert report["accounts"] == 2
    assert report["people"] == 3
    assert report["links"] == 3
    assert report["opted_out"] == 1
    assert report["consent_unknown"] == 1

    with db.tenant_tx(str(tenant["id"])) as cur:
        cur.execute("select name, domain, country from account order by name")
        by_name = {r["name"]: r for r in cur.fetchall()}
    # website -> domain: the transform strips the scheme, the www and the path.
    assert by_name["Northwind Traders SL"]["domain"] == "northwind.example"
    assert by_name["Contoso GmbH"]["country"] == "DE"


@requires_db
def test_consent_survives_the_mapping_vocabulary(db, client, key, tenant):
    client.post("/v1/crm/mappings", json=PUSH_MAPPING, headers=_auth(key))
    client.post("/v1/crm/acme-push/records", json=BATCH, headers=_auth(key))

    with db.tenant_tx(str(tenant["id"])) as cur:
        cur.execute("select email, consent_state from person order by email")
        people = {r["email"]: r["consent_state"] for r in cur.fetchall()}

    # The address was mixed case and padded in the payload.
    assert people["ada@northwind.example"]["email"]["basis"] == "legitimate_interest"
    assert people["grace@northwind.example"]["email"]["opted_out"] is True
    assert people["alan@contoso.example"]["email"]["basis"] == "unknown"


@requires_db
def test_pushing_to_a_provider_that_declares_http_is_refused(client, key):
    """The mapping says this runtime pulls. Accepting a push anyway would
    leave the document describing one system and the runtime running another."""
    pulled = copy.deepcopy(PUSH_MAPPING)
    pulled["metadata"]["provider"] = "acme-pulled"
    pulled["spec"]["transport"] = {
        "kind": "http", "base_url": "https://crm.acme.internal/api",
        "auth": {"kind": "bearer"},
        "accounts": {"path": "/companies", "records": "data"},
        "contacts": {"path": "/people", "records": "data"},
    }
    assert client.post("/v1/crm/mappings", json=pulled,
                       headers=_auth(key)).status_code == 201

    response = client.post("/v1/crm/acme-pulled/records", json=BATCH, headers=_auth(key))
    assert response.status_code == 409
    assert "declares transport 'http'" in response.json()["detail"]


@requires_db
def test_a_push_reports_the_mapping_caveat(client, key):
    client.post("/v1/crm/mappings", json=_without_consent(), headers=_auth(key))
    report = client.post("/v1/crm/acme-blind/records", json=BATCH, headers=_auth(key)).json()

    assert report["people"] == 3
    assert report["consent_unknown"] == 3
    assert any("does not expose opt-out state" in c for c in report["caveats"])
    assert any("tenant-authored mapping" in c for c in report["caveats"])


@requires_db
def test_pushing_the_same_batch_twice_is_not_three_more_people(db, client, key, tenant):
    client.post("/v1/crm/mappings", json=PUSH_MAPPING, headers=_auth(key))
    client.post("/v1/crm/acme-push/records", json=BATCH, headers=_auth(key))
    second = client.post("/v1/crm/acme-push/records", json=BATCH, headers=_auth(key)).json()

    assert second["people"] == 3
    with db.tenant_tx(str(tenant["id"])) as cur:
        cur.execute("select count(*) as n from account")
        assert cur.fetchone()["n"] == 2
        cur.execute("select count(*) as n from person")
        assert cur.fetchone()["n"] == 3


@requires_db
def test_a_mapping_cannot_claim_a_built_in_connector_name(client, key):
    """A built-in source wins the name wherever it is resolved, so a mapping
    claiming it would store cleanly and then never be used."""
    from runtime.cli import install_default_connectors

    install_default_connectors()
    squatting = copy.deepcopy(PUSH_MAPPING)
    squatting["metadata"]["provider"] = "hubspot"
    response = client.post("/v1/crm/mappings", json=squatting, headers=_auth(key))
    assert response.status_code == 409
    assert "cannot claim its name" in response.json()["detail"]


@requires_db
def test_a_base_url_pointing_at_the_runtime_is_refused_at_publish(client, key):
    """The request would be made with this runtime's network position, and
    169.254.169.254 answers with the instance's own credentials."""
    hostile = copy.deepcopy(PUSH_MAPPING)
    hostile["metadata"]["provider"] = "acme-ssrf"
    hostile["spec"]["transport"] = {
        "kind": "http", "base_url": "http://169.254.169.254/latest/meta-data",
        "accounts": {"path": "/", "records": "data"},
        "contacts": {"path": "/", "records": "data"},
    }
    response = client.post("/v1/crm/mappings", json=hostile, headers=_auth(key))
    assert response.status_code == 422
    assert "loopback or link-local" in response.json()["detail"]

    assert client.get("/v1/crm/mappings", headers=_auth(key)).json() == []
