"""Inbound webhooks: the half of the loop that makes lift measurable.

Without these, outcomes arrive only by hand, and a product whose central claim
is measured incrementality depends on someone remembering to post them.

The tests that matter most are the ones about opting out. This repository has
already found the same failure four times in other disguises — a contact asks
not to be contacted, and tomorrow's step still goes out — so an opt-out here is
asserted to suppress, exit and cancel, not two of the three.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from runtime.api.app import create_app
from runtime.engine import enroll
from runtime.engine.worker import Worker
from runtime.provision import create_webhook_endpoint, store_connection
from runtime.repo import actions, enrollments, entities, ledger, programs
from tests.conftest import SECRET
from tests.test_runtime_engine import DISPATCH_SPEC, _account_with_contact, _ingest, _publish


@pytest.fixture
def client(db):
    return TestClient(create_app(db, secret_key=SECRET), raise_server_exceptions=False)


@pytest.fixture
def endpoint(db, tenant):
    return create_webhook_endpoint(db, str(tenant["id"]), provider="smartlead",
                                   secret_key=SECRET)


def _post(client, endpoint, payload, *, secret=None, header="x-smartlead-signature"):
    body = json.dumps(payload).encode()
    signature = hmac.new((secret or endpoint["secret"]).encode(), body,
                         hashlib.sha256).hexdigest()
    return client.post(endpoint["path"].replace("/webhooks/", "/v1/webhooks/"),
                       content=body, headers={header: signature,
                                              "content-type": "application/json"})


# -- authentication ------------------------------------------------------

def test_an_unsigned_event_is_rejected(client, endpoint):
    response = client.post(endpoint["path"].replace("/webhooks/", "/v1/webhooks/"),
                           json={"event_type": "reply"})
    assert response.status_code == 401


def test_a_wrong_signature_is_rejected(client, endpoint):
    """An unauthenticated webhook that records outcomes lets anyone who learns
    the URL move a customer's measured lift."""
    assert _post(client, endpoint, {"event_type": "reply"},
                 secret="not-the-secret").status_code == 401


def test_an_unknown_token_is_indistinguishable_from_a_revoked_one(client):
    """Anything more specific is an oracle for guessing tokens."""
    assert client.post("/v1/webhooks/made-up-token", json={}).status_code == 404


def test_the_signature_is_checked_against_the_raw_body(client, endpoint):
    """JSON round-tripping reorders keys, and a signature over the result
    never matches."""
    payload = {"z": 1, "a": 2, "event_type": "open"}
    assert _post(client, endpoint, payload).status_code == 202


# -- effects -------------------------------------------------------------

def _running_enrollment(db, tenant, fake):
    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=DISPATCH_SPEC)
        account, person = _account_with_contact(cur, tid)
        results = _ingest(cur, tid, account["id"], score=70)
    Worker(db, secret_key=SECRET).tick([tid])
    with db.tenant_tx(tid) as cur:
        cur.execute("select idempotency_key, enrollment_id from touch")
        touch = cur.fetchone()
        # Queue the next step so there is work an opt-out has to cancel.
        enrollment = enrollments.get(cur, results[0].enrollment_id)
        program = programs.get(cur, str(enrollment["program_id"]))
        from runtime.engine import planner

        cur.execute("update enrollment set next_run_at = now() where id = %s",
                    (enrollment["id"],))
        planner.plan_next(cur, tid, enrollments.get(cur, results[0].enrollment_id), program)
    return tid, person, touch


def test_an_opt_out_suppresses_exits_and_cancels(db, tenant, fake, client, endpoint):
    """All three, or the contact is contacted again tomorrow."""
    tid, person, touch = _running_enrollment(db, tenant, fake)
    with db.tenant_tx(tid) as cur:
        assert actions.pending_count(cur) >= 1, "there must be queued work to cancel"

    response = _post(client, endpoint, {
        "event_type": "unsubscribe", "id": f"evt-{uuid.uuid4().hex}",
        "to_email": str(person["email"]),
        "lead": {"custom_fields": {"zolts_idempotency_key": touch["idempotency_key"]}}})
    assert response.status_code == 202, response.text
    effects = set(response.json()["applied"][0]["effects"])
    assert {"suppression.email", "consent.opted_out", "enrollment.exited",
            "actions.cancelled"} <= effects

    with db.tenant_tx(tid) as cur:
        assert actions.pending_count(cur) == 0
        assert enrollments.get(cur, str(touch["enrollment_id"]))["exit_reason"] == "opted_out"
        assert entities.suppressed_keys(cur, str(person["email"]), None)
        cur.execute("select consent_state from person where id = %s", (person["id"],))
        assert cur.fetchone()["consent_state"]["email"]["opted_out"] is True


def test_a_suppressed_contact_is_then_denied_by_the_policy_gate(db, tenant, fake,
                                                                client, endpoint):
    """The webhook and the gate must agree, or the suppression is decorative."""
    tid, person, touch = _running_enrollment(db, tenant, fake)
    _post(client, endpoint, {"event_type": "unsubscribe", "id": f"evt-{uuid.uuid4().hex}",
                             "to_email": str(person["email"])})
    from runtime.engine import gate

    with db.tenant_tx(tid) as cur:
        cur.execute("select * from person where id = %s", (person["id"],))
        verdict = gate.check(cur, tid, person=cur.fetchone(), channel="email",
                             enrollment_id=None, program_spec={})
    assert not verdict.allowed
    assert "suppress" in verdict.rule_key or "unsubscribed" in verdict.rule_key


def test_a_hard_bounce_suppresses_the_address(db, tenant, fake, client, endpoint):
    """Continuing to send to an address that does not exist is what moves a
    domain's reputation."""
    tid, person, touch = _running_enrollment(db, tenant, fake)
    response = _post(client, endpoint, {
        "event_type": "bounce", "id": f"evt-{uuid.uuid4().hex}",
        "to_email": str(person["email"]),
        "lead": {"custom_fields": {"zolts_idempotency_key": touch["idempotency_key"]}}})
    effects = set(response.json()["applied"][0]["effects"])
    assert {"touch.bounced", "person.invalid", "suppression.email"} <= effects


def test_a_reply_records_an_outcome(db, tenant, fake, client, endpoint):
    tid, person, touch = _running_enrollment(db, tenant, fake)
    response = _post(client, endpoint, {
        "event_type": "reply", "id": f"evt-{uuid.uuid4().hex}",
        "to_email": str(person["email"]),
        "lead": {"custom_fields": {"zolts_idempotency_key": touch["idempotency_key"]}}})
    effects = set(response.json()["applied"][0]["effects"])
    assert "outcome.reply_positive" in effects and "touch.replied" in effects
    with db.tenant_tx(tid) as cur:
        cur.execute("select count(*) as n from outcome")
        assert cur.fetchone()["n"] == 1


def test_a_retried_event_is_applied_once(db, tenant, fake, client, endpoint):
    """Providers retry. A retried conversion would move the measured lift."""
    tid, person, touch = _running_enrollment(db, tenant, fake)
    payload = {"event_type": "reply", "id": "stable-event-id",
               "to_email": str(person["email"]),
               "lead": {"custom_fields": {"zolts_idempotency_key": touch["idempotency_key"]}}}
    first = _post(client, endpoint, payload).json()
    second = _post(client, endpoint, payload).json()
    assert len(first["applied"]) == 1 and second["duplicates"] == 1
    with db.tenant_tx(tid) as cur:
        cur.execute("select count(*) as n from outcome")
        assert cur.fetchone()["n"] == 1


def test_an_unmapped_event_is_stored_and_not_guessed_at(db, tenant, client, endpoint):
    """A wrong mapping writes an outcome that silently moves a measured lift."""
    response = _post(client, endpoint, {"event_type": "something_new",
                                        "id": f"evt-{uuid.uuid4().hex}"})
    assert response.status_code == 202
    assert response.json()["applied"][0]["effects"] == []
    with db.tenant_tx(str(tenant["id"])) as cur:
        cur.execute("select event_type, payload from inbound_event")
        row = cur.fetchone()
    assert row["event_type"] == "something_new"
    assert row["payload"]["event_type"] == "something_new", "the raw body is the record"


def test_a_batch_is_the_same_work_as_a_single_event(db, tenant, fake, client, endpoint):
    tid, person, touch = _running_enrollment(db, tenant, fake)
    response = _post(client, endpoint, [
        {"event_type": "open", "id": "b1", "to_email": str(person["email"]),
         "lead": {"custom_fields": {"zolts_idempotency_key": touch["idempotency_key"]}}},
        {"event_type": "reply", "id": "b2", "to_email": str(person["email"]),
         "lead": {"custom_fields": {"zolts_idempotency_key": touch["idempotency_key"]}}},
    ])
    assert response.json()["received"] == 2
    assert len(response.json()["applied"]) == 2


def test_events_do_not_leak_between_tenants(db, tenant, other_tenant, client):
    mine = create_webhook_endpoint(db, str(tenant["id"]), provider="smartlead",
                                   secret_key=SECRET)
    _post(client, mine, {"event_type": "open", "id": f"evt-{uuid.uuid4().hex}"})
    with db.tenant_tx(str(other_tenant["id"])) as cur:
        cur.execute("select count(*) as n from inbound_event")
        assert cur.fetchone()["n"] == 0


# -- HubSpot's scheme ----------------------------------------------------

def test_hubspot_v3_signatures_are_verified(db, tenant, client):
    """HubSpot signs method + uri + body + timestamp, base64, not the body alone."""
    import base64

    endpoint = create_webhook_endpoint(db, str(tenant["id"]), provider="hubspot",
                                       secret_key=SECRET)
    path = endpoint["path"].replace("/webhooks/", "/v1/webhooks/")
    body = json.dumps({"subscriptionType": "contact.unsubscribed",
                       "eventId": 991, "email": "x@example.com"}).encode()
    timestamp = "1900000000000"
    url = f"http://testserver{path}"
    signed = base64.b64encode(
        hmac.new(endpoint["secret"].encode(),
                 ("POST" + url + body.decode() + timestamp).encode(),
                 hashlib.sha256).digest()).decode()
    ok = client.post(path, content=body,
                     headers={"x-hubspot-signature-v3": signed,
                              "x-hubspot-request-timestamp": timestamp,
                              "content-type": "application/json"})
    assert ok.status_code == 202, ok.text

    bad = client.post(path, content=body,
                      headers={"x-hubspot-signature-v3": "wrong",
                               "x-hubspot-request-timestamp": timestamp,
                               "content-type": "application/json"})
    assert bad.status_code == 401



def test_a_hubspot_deal_records_an_outcome_with_its_value(db, tenant, fake, client):
    import base64

    tid, person, touch = _running_enrollment(db, tenant, fake)
    endpoint = create_webhook_endpoint(db, tid, provider="hubspot", secret_key=SECRET)
    path = endpoint["path"].replace("/webhooks/", "/v1/webhooks/")
    body = json.dumps({
        "subscriptionType": "deal.creation", "eventId": 4242,
        "properties": {"zolts_idempotency_key": touch["idempotency_key"],
                       "amount": "24000"}}).encode()
    timestamp = "1900000000000"
    signed = base64.b64encode(
        hmac.new(endpoint["secret"].encode(),
                 ("POST" + f"http://testserver{path}" + body.decode() + timestamp).encode(),
                 hashlib.sha256).digest()).decode()
    response = client.post(path, content=body,
                           headers={"x-hubspot-signature-v3": signed,
                                    "x-hubspot-request-timestamp": timestamp,
                                    "content-type": "application/json"})
    assert response.status_code == 202, response.text
    assert "outcome.opp_created" in response.json()["applied"][0]["effects"]
    with db.tenant_tx(tid) as cur:
        cur.execute("select type, value_micros from outcome")
        row = cur.fetchone()
    assert row["type"] == "opp_created" and row["value_micros"] == 24_000_000_000


def test_an_endpoint_without_a_secret_accepts_nothing(db, tenant, client):
    """Default deny. An unauthenticated webhook that records outcomes is a way
    for anyone who learns the URL to move a customer's measured lift."""
    endpoint = create_webhook_endpoint(db, str(tenant["id"]), provider="smartlead",
                                       secret_key=SECRET)
    with db.tenant_tx(str(tenant["id"])) as cur:
        cur.execute("update webhook_endpoint set secret_enc = null")
    response = client.post(endpoint["path"].replace("/webhooks/", "/v1/webhooks/"),
                           json={"event_type": "reply"})
    assert response.status_code == 401
    assert "signing secret" in response.json()["detail"]
