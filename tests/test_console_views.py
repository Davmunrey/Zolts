"""The screens an operator actually needs, and the ones that were not there.

The console had Programs, Review queue and Sending. The rail also offered
Signals, Experiments, Spend, Policy and Audit log, and clicking any of them
did nothing — a nav that promises capability the surface does not have, which
teaches an operator that clicking things here is pointless.

Worse than a missing screen: `/v1/accounts` and `/v1/people` were POST-only.
A tenant could create a prospect and had no way to read one back, so the list
they would live in was not merely unbuilt, it was unbuildable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.api.app import create_app
from runtime.api import console
from runtime.provision import issue_api_key
from tests.conftest import requires_db
from tests.test_runtime_engine import _account_with_contact

CONSOLE = Path(__file__).resolve().parent.parent / "design" / "console.html"


@pytest.fixture
def client(db):
    return TestClient(create_app(db), raise_server_exceptions=False)


@pytest.fixture
def key(db, tenant):
    return issue_api_key(db, str(tenant["id"]), "test", []).token


# -- the nav promises nothing it cannot do -------------------------------

def test_every_rail_entry_leads_somewhere():
    """A link that does nothing is worse than an absent one."""
    import re

    markup = CONSOLE.read_text()
    nav = markup[markup.index('<nav aria-label="Primary">'):markup.index("</nav>")]
    entries = re.findall(r"<a\b[^>]*>", nav)
    assert entries, "the rail has no entries"

    dead = [a for a in entries if "data-view=" not in a]
    assert not dead, f"{len(dead)} rail entries have no view behind them: {dead}"

    declared = set(re.findall(r'data-view="([a-z]+)"', nav))
    rendered = set(re.findall(r'state\.view === "([a-z]+)"', markup))
    # `programs` is the default and needs no branch.
    assert declared - {"programs"} <= rendered, (
        f"the rail offers views nothing renders: {sorted(declared - rendered - {'programs'})}")


def test_the_rail_count_and_the_view_count_agree_on_signals():
    """Two things writing one element is how a rail comes to contradict the
    screen it links to."""
    markup = CONSOLE.read_text()
    assert markup.count('put("nav-signals"') == 0, (
        "the old distinct-trigger count still writes the signals badge")
    assert markup.count('getElementById("nav-signals")') == 1


# -- prospects -----------------------------------------------------------

@requires_db
def test_a_tenant_can_read_back_the_prospects_it_created(db, client, key, tenant):
    """POST-only meant a tenant could create a prospect and never see one."""
    created = client.post("/v1/accounts", headers={"x-api-key": key},
                          json={"name": "Acme", "domain": "acme.example",
                                "country": "ES"})
    assert created.status_code == 201

    body = client.get("/v1/accounts", headers={"x-api-key": key}).json()
    assert [a["name"] for a in body["accounts"]] == ["Acme"]
    assert body["missingCounts"]["firmographics"] == 1, (
        "an account with no employee band or industry was reported complete")


@requires_db
def test_missing_is_decided_by_the_rule_that_spends_the_money(db, tenant):
    """A screen with its own idea of what is missing offers to buy a field the
    engine will then decline to buy."""
    from runtime.connectors.dataprovider import unresolved

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        account, person = _account_with_contact(cur, tid)
        view = console.prospects_view(cur)
        cur.execute("select * from person where id = %s", (person["id"],))
        row = dict(cur.fetchone())

    shown = {p["id"]: p for p in view["people"]}[str(person["id"])]
    for field in ("email", "phone"):
        assert (field in shown["missing"]) == unresolved(row, field), (
            f"the screen and the engine disagree about {field}")


@requires_db
def test_an_opted_out_contact_is_shown_rather_than_hidden(db, tenant):
    """An operator who cannot see them wonders why the count does not add up."""
    from runtime.repo import entities

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        entities.upsert_person(
            cur, tid, email="gone@acme.example", full_name="Gone Away",
            consent_state={"email": {"basis": "legitimate_interest",
                                     "opted_out": True}})
        view = console.prospects_view(cur)

    assert len(view["people"]) == 1
    assert view["people"][0]["optedOut"] is True


# -- enrichment from the surface -----------------------------------------

@requires_db
def test_enrichment_is_reachable_without_the_cli(db, client, key, tenant):
    """It existed only as `zolts enrich`, so the field a customer is paying
    credits for could be bought by an operator with a shell and nobody else."""
    from runtime.connectors import dataprovider
    from runtime.connectors.fake import FakeDataProvider

    tid = str(tenant["id"])
    dataprovider.register_provider(FakeDataProvider(key="p", fields=("email",)))
    try:
        with db.tenant_tx(tid) as cur:
            cur.execute(
                "insert into data_provider (tenant_id, key, fields,"
                " unit_cost_micros) values (zolts_internal.current_tenant(),"
                " 'p', '{email}', 20000)")
            _, person = _account_with_contact(cur, tid)
            cur.execute("update person set email = null where id = %s",
                        (person["id"],))

        raw = client.post("/v1/enrich", headers={"x-api-key": key},
                          json={"field": "email", "ids": [str(person["id"])]})
        assert raw.status_code == 200, f"{raw.status_code}: {raw.text[:300]}"
        body = raw.json()
    finally:
        dataprovider._SOURCES.pop("p", None)

    assert body["asked"] == 1 and body["resolved"] == 1
    assert body["creditsBilled"] == 8.0
    assert "not billed to the tenant" in body["note"]


@requires_db
def test_enrichment_over_http_needs_a_write_scope(db, client, tenant):
    """It spends money."""
    read_only = issue_api_key(db, str(tenant["id"]), "reader", ["read"]).token
    response = client.post("/v1/enrich", headers={"x-api-key": read_only},
                           json={"field": "email", "ids": ["00000000-0000-0000-0000-000000000001"]})
    assert response.status_code == 403


# -- spend ---------------------------------------------------------------

@requires_db
def test_spend_shows_the_number_the_customer_is_charged_on(db, tenant):
    """`/v1/billing/current` answered this since billing existed and no screen
    asked it, so it was visible only to whoever ran curl."""
    from runtime import metering

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        cur.execute("select * from tenant where id = %s", (tid,))
        row = cur.fetchone()
        metering.meter(cur, row, kind="email.send", units=120)
        view = console.spend_view(cur, row)

    assert view["plan"] == "starter"
    assert view["consumed"] == 120.0
    assert view["remaining"] == 14_880.0
    assert view["byKind"] == [{"kind": "email.send", "credits": 120.0,
                               "events": 1, "costEur": 0.0}]
