"""Signing up a tenant without a shell.

Creating a tenant used to require database access, which put a human in the
loop for every partner. These tests cover what replaced it, and in particular
the parts a demo would never exercise: a token redeemed twice, a token redeemed
twice at the same moment, and a blueprint whose starter set is empty.
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from runtime import onboarding
from runtime.api.app import create_app
from tests.conftest import requires_db


@pytest.fixture
def client(db):
    return TestClient(create_app(db), raise_server_exceptions=False)


# -- minting -------------------------------------------------------------

@requires_db
def test_the_token_is_never_stored(db):
    issued = onboarding.mint(db, company_name="Northwind")
    with db.admin_tx() as cur:
        cur.execute("select token_hash, prefix from invitation where id = %s",
                    (issued.id,))
        row = cur.fetchone()
    assert issued.token not in row["token_hash"]
    assert row["token_hash"] == __import__("hashlib").sha256(
        issued.token.encode()).hexdigest()
    assert issued.token.startswith(row["prefix"])


# -- redeeming -----------------------------------------------------------

@requires_db
def test_an_invitation_becomes_a_tenant_with_a_key_and_draft_programs(db, client):
    issued = onboarding.mint(db, company_name="Northwind Traders",
                             blueprint_id="b2b-saas-sales-led")
    response = client.post("/v1/signup", json={"token": issued.token})
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["tenant"]["slug"] == "northwind-traders"
    assert body["tenant"]["blueprint"] == "b2b-saas-sales-led"
    assert body["api_key"].startswith("zk_")
    # Draft, never active. A tenant contacting people before anyone read a
    # program is not onboarding.
    assert body["programs"] and all(p["status"] == "draft" for p in body["programs"])

    listed = client.get("/v1/programs", headers={"x-api-key": body["api_key"]})
    assert listed.status_code == 200
    assert {p["key"] for p in listed.json()} == {p["key"] for p in body["programs"]}


@requires_db
def test_a_second_redemption_is_refused(db, client):
    issued = onboarding.mint(db, company_name="Contoso")
    assert client.post("/v1/signup", json={"token": issued.token}).status_code == 201

    replay = client.post("/v1/signup", json={"token": issued.token})
    assert replay.status_code == 400
    assert replay.json()["detail"] == onboarding.REJECTED

    with db.admin_tx() as cur:
        cur.execute("select count(*) as n from tenant where name = 'Contoso'")
        assert cur.fetchone()["n"] == 1


@requires_db
def test_invalid_expired_and_redeemed_are_indistinguishable(db, client):
    """Telling them apart tells a caller which tokens exist."""
    redeemed = onboarding.mint(db, company_name="Redeemed Co")
    client.post("/v1/signup", json={"token": redeemed.token})

    expired = onboarding.mint(db, company_name="Expired Co", ttl_days=14)
    with db.admin_tx() as cur:
        cur.execute("update invitation set expires_at = now() - interval '1 day'"
                    " where id = %s", (expired.id,))

    answers = {
        client.post("/v1/signup", json={"token": t}).json()["detail"]
        for t in (redeemed.token, expired.token, "zi_" + "x" * 40)
    }
    assert answers == {onboarding.REJECTED}


@requires_db
def test_two_simultaneous_redemptions_produce_one_tenant(db):
    """The window between reading a valid row and marking it redeemed is where
    a second request slips through. `for update` closes it."""
    issued = onboarding.mint(db, company_name="Race Condition Ltd")
    results: list[object] = []
    barrier = threading.Barrier(2)

    def attempt():
        barrier.wait()
        try:
            results.append(onboarding.redeem(db, issued.token))
        except onboarding.InvitationError as exc:
            results.append(exc)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    succeeded = [r for r in results if isinstance(r, dict)]
    refused = [r for r in results if isinstance(r, onboarding.InvitationError)]
    assert len(succeeded) == 1, results
    assert len(refused) == 1, results

    with db.admin_tx() as cur:
        cur.execute("select count(*) as n from tenant where name = 'Race Condition Ltd'")
        assert cur.fetchone()["n"] == 1


@requires_db
def test_a_redeemed_invitation_is_never_half_written(db, client):
    """A row claiming a tenant must say when, and a row saying when must name
    one. The table refuses anything else, which is how the two-step claim this
    replaced was caught."""
    issued = onboarding.mint(db, company_name="Halfway Inc")
    client.post("/v1/signup", json={"token": issued.token})
    with db.admin_tx() as cur:
        cur.execute("select redeemed_at, redeemed_tenant_id from invitation"
                    " where id = %s", (issued.id,))
        row = cur.fetchone()
    assert row["redeemed_at"] is not None
    assert row["redeemed_tenant_id"] is not None


# -- what the invitee is told --------------------------------------------

@requires_db
def test_the_form_can_be_described_without_redeeming(db, client):
    issued = onboarding.mint(db, company_name="Preview Co", region="us")
    described = client.get(f"/v1/signup/{issued.token}")
    assert described.status_code == 200
    assert described.json()["company_name"] == "Preview Co"
    assert described.json()["region"] == "us"

    # Describing must not consume it.
    assert client.post("/v1/signup", json={"token": issued.token}).status_code == 201


@requires_db
def test_describing_an_unknown_token_reveals_nothing(client):
    response = client.get("/v1/signup/zi_" + "x" * 40)
    assert response.status_code == 404
    assert response.json()["detail"] == onboarding.REJECTED


@requires_db
def test_a_blueprint_with_no_starter_program_says_so(db, client):
    """Seven of the eleven blueprints ship no example program. An empty list
    with no sentence beside it is how a new tenant concludes it is broken."""
    issued = onboarding.mint(db, company_name="Regulated Bank",
                             blueprint_id="fintech-regulated")
    body = client.post("/v1/signup", json={"token": issued.token}).json()

    assert body["programs"] == []
    assert "no starter program ships for 'fintech-regulated'" in body["programs_note"]
    # It names what the blueprint promised, so the note is actionable.
    assert "regulatory-deadline-play" in body["programs_note"]


@requires_db
def test_an_unknown_blueprint_is_named_not_defaulted(db, client):
    """A tenant running the wrong blueprint gets the wrong policy, and finds
    out by sending something."""
    issued = onboarding.mint(db, company_name="Typo Ltd")
    response = client.post("/v1/signup",
                           json={"token": issued.token, "blueprint_id": "b2b-sass"})
    assert response.status_code == 400
    assert "unknown blueprint 'b2b-sass'" in response.json()["detail"]

    # And the invitation survives the mistake.
    assert client.get(f"/v1/signup/{issued.token}").status_code == 200


@requires_db
def test_a_colliding_company_name_still_gets_a_tenant(db, client):
    first = onboarding.mint(db, company_name="Acme")
    second = onboarding.mint(db, company_name="Acme")
    a = client.post("/v1/signup", json={"token": first.token}).json()
    b = client.post("/v1/signup", json={"token": second.token}).json()

    assert a["tenant"]["slug"] == "acme"
    assert b["tenant"]["slug"].startswith("acme-")
    assert a["tenant"]["id"] != b["tenant"]["id"]


def test_slugify_is_lossy_and_safe():
    assert onboarding.slugify("Northwind Traders, S.L.") == "northwind-traders-s-l"
    assert onboarding.slugify("  ") == "tenant"
    assert onboarding.slugify("///") == "tenant"
    assert len(onboarding.slugify("x" * 200)) == 40


@requires_db
def test_a_signed_up_tenant_sees_only_its_own_programs(db, client):
    one = onboarding.mint(db, company_name="Tenant One")
    two = onboarding.mint(db, company_name="Tenant Two")
    key_one = client.post("/v1/signup", json={"token": one.token}).json()["api_key"]
    key_two = client.post("/v1/signup", json={"token": two.token}).json()["api_key"]

    ids_one = {p["id"] for p in client.get("/v1/programs",
                                           headers={"x-api-key": key_one}).json()}
    ids_two = {p["id"] for p in client.get("/v1/programs",
                                           headers={"x-api-key": key_two}).json()}
    assert ids_one and ids_two
    assert not (ids_one & ids_two)
