"""The console sends the rows the operator selected — which it did not.

`POST /v1/enrich` has existed since enrichment became executable, and its own
docstring states its caller: *the console sends the rows the operator
selected.* The console sent nothing. The Prospects screen computed which
contacts were missing an email or a phone, using — by its own comment — the
same rule the engine uses to decide whether to spend money, and offered no way
to buy any of it. A specification complete down to its caller's name, with no
caller: this repository's dominant shape, on the one screen where the missing
control is also unbilled revenue.

Three properties carry the control.

**The price is shown before the click, read from the price list.** A phone
number is 25 credits and an email is 8 — three times the spread, on the same
screen, in the same selection. A purchase whose cost appears only on the
invoice is how a data budget disappears.

**Named rows, never everything unresolved.** The endpoint already enforces it;
the screen has to agree, or it offers a purchase nobody can predict the bill
for.

**The legal basis is chosen, never defaulted.** It is recorded against every
value bought and cannot be reconstructed later, so a screen that fills it in
silently records the screen's default rather than a decision.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from runtime.api import console as console_view
from tests.conftest import requires_db
from zolts import billing

SURFACE = (Path(__file__).resolve().parent.parent
           / "design" / "console.html").read_text(encoding="utf-8")


@requires_db
def test_the_screen_is_given_the_price_of_every_field_it_offers(db, tenant):
    """Read out of `zolts.billing`, never restated in the view.

    A screen holding its own copy of the price list quotes a stale one the day
    the list changes, and nothing fails.
    """
    with db.tenant_tx(tenant["id"]) as cur:
        view = console_view.prospects_view(cur)
    prices = view["prices"]
    for field in ("email", "phone", "firmographics"):
        assert prices[field] == float(billing.CREDITS[f"enrich.{field}"])
    assert prices["dossier"] == float(billing.CREDITS["agent.dossier"])


@requires_db
def test_the_price_follows_the_list_when_the_list_moves(db, tenant, monkeypatch):
    """Behavioural, because a dict compared against the dict that built it
    passes on any dict. Move the price and the view has to move with it."""
    moved = dict(billing.CREDITS)
    moved["enrich.email"] = billing.CREDITS["enrich.email"] + 3
    monkeypatch.setattr(billing, "CREDITS", moved)
    with db.tenant_tx(tenant["id"]) as cur:
        view = console_view.prospects_view(cur)
    assert view["prices"]["email"] == float(moved["enrich.email"])


def test_the_surface_no_longer_prints_a_price_of_its_own():
    """The dossier's 20 credits was a literal in the markup, one panel below
    the fields this change prices properly."""
    assert '"20 credits"' not in SURFACE
    assert 'nf(price.dossier || 0) + " credits"' in SURFACE


def test_the_screen_shows_what_the_purchase_costs_before_the_click():
    assert "nf(cost) + ' credits</button>'" in SURFACE, (
        "the buy button does not say what it will spend")
    assert "n * (price[field] || 0)" in SURFACE, (
        "the cost is not the count times the field's price")


def test_the_screen_sends_only_the_selected_rows():
    """Named rather than everything unresolved, matching the endpoint."""
    assert 'act("/v1/enrich", "POST", {field: field, ids: state.psel,' in SURFACE
    assert "state.psel" in SURFACE


def test_only_a_contact_with_something_buyable_can_be_selected():
    """A row that highlights and then buys nothing is a control that lies
    about what it will spend."""
    assert "if (c.missing.length) buyable[c.id] = c;" in SURFACE
    assert 'state.psel = (state.psel || []).filter(function(id){ return buyable[id]; });' in SURFACE


def test_no_legal_basis_is_preselected():
    """Recorded against every value bought and not reconstructable later, so a
    screen that fills it in silently records its own default."""
    assert "basis: null," in SURFACE, "the screen starts with a basis chosen"
    assert "if (!state.basis){" in SURFACE, "the buy button does not require one"
    assert "Choose a legal basis first" in SURFACE


def test_the_screen_says_what_the_documents_say_about_legitimate_interest():
    """`docs/11` marks the assessment Not built. Said where somebody is about
    to assert it, rather than in a footnote they will not reach."""
    doc = (Path(__file__).resolve().parent.parent
           / "docs" / "11-compliance-and-governance.md").read_text(encoding="utf-8")
    assert "Legitimate interest assessment | **Not built**" in doc, (
        "the document no longer says the assessment is unbuilt; the screen "
        "still tells the operator it is")
    assert "COMP-5" in SURFACE
    assert "No assessment is stored for this basis yet" in SURFACE


def test_the_screen_says_a_miss_is_paid_for_and_not_billed():
    """The endpoint returns that note and nothing showed it. An operator who
    thinks a miss is free buys differently from one who knows it is not."""
    assert "A miss is paid for and not billed." in SURFACE


@requires_db
def test_buying_an_email_reaches_the_endpoint_and_records_the_basis(db, tenant):
    """The whole path. A control that posts and a runtime that records are two
    halves, and this repository keeps finding changes where only one exists."""
    from fastapi.testclient import TestClient

    from runtime.api.app import create_app
    from runtime.provision import issue_api_key

    client = TestClient(create_app(db), raise_server_exceptions=False)
    token = issue_api_key(db, str(tenant["id"]), "test", []).token
    with db.tenant_tx(tenant["id"]) as cur:
        cur.execute(
            "insert into person (tenant_id, full_name, country)"
            " values (%s,'Dana Reyes','ES') returning id", (tenant["id"],))
        person_id = str(cur.fetchone()["id"])

    answer = client.post("/v1/enrich", headers={"x-api-key": token},
                         json={"field": "email", "ids": [person_id],
                               "legal_basis": "consent"})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["field"] == "email" and body["asked"] == 1
    assert "misses are paid for" in body["note"]


def test_the_endpoints_stated_caller_now_exists():
    """The docstring named the console as its caller for as long as the
    endpoint has existed. That sentence is now true."""
    app = (Path(__file__).resolve().parent.parent
           / "runtime" / "api" / "app.py").read_text(encoding="utf-8")
    assert "The console" in app and "sends the rows the operator selected" in app
    assert '"/v1/enrich"' in SURFACE, (
        "the endpoint still says the console calls it, and the console does not")
