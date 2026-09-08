"""The provider documents this repository ships, exercised as documents.

`docs/12` prices `enrich.email`, `zolts/waterfall.py` optimises the order the
runtime buys it in, and every supplier behind that machinery was either the
fake provider the tests register or a document pointing at
`api.enrichment.example`, a host that does not exist (decision 38). A priced
field with no supplier is a specification with no caller wearing a price list.

`examples/providers/hunter.yaml` is the first named one. Nothing here calls
Hunter: a test that spent somebody's credits would fail on the day their
account ran out, so the request and the response shapes are asserted against
a transport this test controls. What it does prove is the part that was
wrong — the request Hunter would receive, and what this runtime makes of the
answer, including the score it reports as a confidence (D-47).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml

from runtime.connectors.dataprovider import Lookup, ProviderError
from runtime.connectors.declarative_provider import (DeclarativeDataProvider,
                                                     ProviderDocumentError, validate)
from tests.conftest import requires_db

PROVIDERS = Path(__file__).resolve().parent.parent / "examples" / "providers"
HUNTER = PROVIDERS / "hunter.yaml"

# One answer, in the shape Hunter returns it: a score out of 100, and the
# address nested under `data`.
FOUND = {"data": {"email": "ana.torres@northwind.example", "score": 76,
                  "first_name": "Ana", "last_name": "Torres",
                  "verification": {"status": "valid"}},
         "meta": {"params": {}}}
# Hunter answers 200 with a null email when it cannot find one. That is an
# answer, and the difference between it and an error is the whole reason the
# optimiser can learn anything.
NOT_FOUND = {"data": {"email": None, "score": 0}, "meta": {"params": {}}}

PERSON = {"first_name": "Ana", "last_name": "Torres", "full_name": "Ana Torres"}
ACCOUNT = {"domain": "northwind.example", "name": "Northwind Traders", "country": "ES"}


def _document(path: Path = HUNTER) -> dict:
    return yaml.safe_load(path.read_text())


def _provider(handler, path: Path = HUNTER) -> DeclarativeDataProvider:
    return DeclarativeDataProvider(document=_document(path),
                                   client=httpx.Client(transport=httpx.MockTransport(handler)))


# -- every document that ships is a document that loads ---------------------

@pytest.mark.parametrize("path", sorted(PROVIDERS.glob("*.yaml")), ids=lambda p: p.stem)
def test_every_shipped_provider_document_is_valid(path):
    """A document in this directory is one an operator will register. One that
    only validates after somebody edits it is worse than an absent one."""
    document = validate(_document(path))
    metadata = document.get("metadata") or {}
    assert metadata.get("provider"), f"{path.name} names no provider"
    assert metadata.get("description"), f"{path.name} says nothing about what it resolves"
    assert (document["spec"].get("unit_cost_micros") or 0) > 0, (
        f"{path.name} costs nothing, and the waterfall would put it first for that reason")


def test_hunter_resolves_one_field_and_claims_no_others():
    """A document claiming `phone` would make the waterfall believe it has two
    independent suppliers for a field it has none for."""
    provider = DeclarativeDataProvider(document=_document())
    assert provider.capabilities.provider == "hunter"
    assert provider.capabilities.fields == ("email",)
    assert provider.capabilities.billed_on_miss is False


# -- the request Hunter would receive ---------------------------------------

def test_the_key_travels_where_this_api_takes_it_and_the_question_is_answerable():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=FOUND)

    found = _provider(handler).resolve(Lookup("email", PERSON, ACCOUNT), "hunter-key", {})
    assert found.hit

    [request] = seen
    assert request.url.path == "/v2/email-finder"
    assert dict(request.url.params) == {
        "domain": "northwind.example", "first_name": "Ana", "last_name": "Torres",
        "api_key": "hunter-key"}
    assert "authorization" not in request.headers, (
        "the key went as a bearer token as well; this API takes a query parameter")


def test_a_person_with_no_company_domain_is_not_a_call_anybody_pays_for():
    """Hunter finds an address from a name *and* a domain. Asking without one
    is a request that cannot hit, and a request that cannot hit is not sent."""
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError(f"a lookup was sent with nothing to go on: {request.url}")

    found = _provider(handler).resolve(Lookup("email", {}, {}), "hunter-key", {})
    assert not found.hit and found.cost_micros == 0
    assert found.detail == {"skipped": "no known facts to ask with"}


# -- what this runtime makes of the answer ----------------------------------

def test_a_score_out_of_a_hundred_is_not_certainty():
    """D-47. `min(1.0, float(raw))` turned Hunter's 76 into 1.0, and 1.0 is
    what the dossier shows an operator and what an accuracy floor is read
    against. The document states its own scale."""
    found = _provider(lambda r: httpx.Response(200, json=FOUND)).resolve(
        Lookup("email", PERSON, ACCOUNT), "hunter-key", {})
    assert found.hit
    assert found.values == {"email": "ana.torres@northwind.example"}
    assert found.confidence == pytest.approx(0.76)
    assert found.cost_micros == 34_000


def test_a_score_above_the_declared_scale_is_still_a_confidence():
    """Clamped, not rejected: a provider returning 120 is a provider whose
    documentation is wrong, and the lookup still resolved an address."""
    found = _provider(lambda r: httpx.Response(200, json={"data": {"email": "a@b.example",
                                                                  "score": 120}})).resolve(
        Lookup("email", PERSON, ACCOUNT), "hunter-key", {})
    assert found.confidence == 1.0


def test_no_address_is_a_miss_and_a_miss_is_not_billed_to_anybody():
    found = _provider(lambda r: httpx.Response(200, json=NOT_FOUND)).resolve(
        Lookup("email", PERSON, ACCOUNT), "hunter-key", {})
    assert not found.hit
    assert found.cost_micros == 0, "this document does not declare billed_on_miss"
    assert found.values == {}


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_a_provider_that_could_not_be_asked_is_never_a_miss(status):
    """An error is the absence of an answer. Counting it as a miss would teach
    the optimiser that a broken integration is a bad provider, and it would
    then buy the same field somewhere more expensive for ever."""
    provider = _provider(lambda r: httpx.Response(status, text="no"))
    with pytest.raises(ProviderError):
        provider.resolve(Lookup("email", PERSON, ACCOUNT), "hunter-key", {})


def test_a_scale_the_document_never_uses_is_refused():
    """A `confidence_max` beside no confidence path is a specification with no
    caller, which is this repository's most common defect."""
    document = _document()
    document["spec"]["lookups"]["email"].pop("confidence")
    with pytest.raises(ProviderDocumentError, match="never reads"):
        validate(document)

    document = _document()
    document["spec"]["lookups"]["email"]["confidence_max"] = 0
    with pytest.raises(ProviderDocumentError, match="above zero"):
        validate(document)

    document = _document()
    document["spec"]["lookups"]["email"]["confidence_max"] = "high"
    with pytest.raises(ProviderDocumentError, match="not a number"):
        validate(document)


def test_a_document_without_a_scale_still_reads_a_fractional_confidence():
    """The default stays 1.0, so `example-enrichment.yaml` and every document
    written before the scale existed keep the meaning they had."""
    document = _document()
    document["spec"]["lookups"]["email"].pop("confidence_max")
    provider = DeclarativeDataProvider(
        document=document,
        client=httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"data": {"email": "a@b.example",
                                                         "score": 0.62}}))))
    found = provider.resolve(Lookup("email", PERSON, ACCOUNT), "k", {})
    assert found.confidence == pytest.approx(0.62)


# -- the name the plan will ask for -----------------------------------------

@requires_db
def test_a_document_and_its_registration_answer_to_one_name(db, tenant):
    """D-48. The waterfall asks `get_provider(row["key"])` and the installer
    registered the document under its own metadata name, so a row keyed
    `hunter-io` holding this document was planned and then not found — and
    `LookupError` is not the exception the buy loop catches, so it left the
    step rather than skipping the provider."""
    import json

    from runtime import enrichment

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        cur.execute(
            "insert into data_provider (tenant_id, key, fields, unit_cost_micros, config)"
            " values (zolts_internal.current_tenant(), 'hunter-io', '{email}', 34000, %s)",
            (json.dumps({"mapping": _document()}),))
        with pytest.raises(enrichment.EnrichmentError, match="hunter-io"):
            enrichment.install_declared(cur)

        cur.execute("update data_provider set key = 'hunter' where key = 'hunter-io'")
        assert enrichment.install_declared(cur) == ["hunter"]


@requires_db
def test_the_command_line_refuses_the_mismatch_before_the_row_exists(db, tenant, monkeypatch,
                                                                     capsys):
    from runtime import cli
    from tests.conftest import APP_URL, OWNER_URL, SECRET

    monkeypatch.setenv("ZOLTS_DATABASE_URL", OWNER_URL)
    monkeypatch.setenv("ZOLTS_APP_DATABASE_URL", APP_URL)
    monkeypatch.setenv("ZOLTS_SECRET_KEY", SECRET)
    tid = str(tenant["id"])
    argv = ["data-provider", "--tenant", tid, "--fields", "email",
            "--cost-micros", "34000", "--mapping", str(HUNTER)]

    assert cli.main([*argv, "--key", "hunter-io"]) == 2
    assert "the document describes 'hunter'" in capsys.readouterr().err
    with db.tenant_tx(tid) as cur:
        cur.execute("select count(*) as n from data_provider")
        assert cur.fetchone()["n"] == 0, "the refused registration was stored anyway"

    assert cli.main([*argv, "--key", "hunter"]) == 0
    with db.tenant_tx(tid) as cur:
        cur.execute("select key, unit_cost_micros, config from data_provider")
        row = cur.fetchone()
    assert row["key"] == "hunter" and row["unit_cost_micros"] == 34_000
    assert row["config"]["mapping"]["metadata"]["provider"] == "hunter"
