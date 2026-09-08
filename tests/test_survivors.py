"""Guards written because a sampled mutation lived.

`scripts/mutation_coverage.py` mutates the runtime and runs the suite that
could catch it. A mutant that survives is a line no test distinguishes from a
different line — so this file is the second half of VER-1: not the measurement,
but what the measurement was for.

Each test names the mutation it kills. Two survivors from the same run are
*not* here and are annotated where they live instead: an equivalent mutant is
answered by saying so, never by a test that asserts a no-op.
"""

from __future__ import annotations

import httpx
import pytest

from runtime import watch
from runtime.connectors import dataprovider
from runtime.connectors.crm import Consent
from runtime.connectors.declarative_provider import DeclarativeDataProvider
from runtime.connectors.pipedrive import PipedriveConnector
from runtime.connectors.salesforce import SalesforceConnector
from tests.conftest import APP_URL, OWNER_URL, requires_db
from tests.test_crm_contract import SALESFORCE_INSTANCE, _salesforce_handler


# -- pipedrive.py:135  `is` -> `is not` ----------------------------------

_PAGE_ONE = {
    "data": [{"id": 1, "name": "First page", "address_country": "ES"}],
    "additional_data": {"pagination": {"more_items_in_collection": True,
                                       "next_start": 1}},
}
_PAGE_TWO = {
    "data": [{"id": 2, "name": "Second page", "address_country": "FR"}],
    "additional_data": {"pagination": {"more_items_in_collection": False}},
}


def test_pipedrive_reads_past_the_first_page(monkeypatch):
    """`next_start is None` ends the walk; `is not None` ended it immediately.

    Pipedrive returns an offset rather than a cursor, and returns it as null on
    the final page even when the flag still says there is more — so the guard
    exists to stop an endless loop. Inverted, it stops on the *first* page that
    has a next offset, which is every page but the last: a sync that silently
    returns one page of a customer's CRM and reports success.

    Nothing paginated this connector before, so the mutation lived.
    """
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("start", 0))
        seen.append(start)
        return httpx.Response(200, json=_PAGE_ONE if start == 0 else _PAGE_TWO)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    import runtime.connectors.http as http_module
    original = http_module.request
    monkeypatch.setattr("runtime.connectors.pipedrive.request",
                        lambda method, url, **kw: original(method, url, client=client, **kw))

    names = [account.name for account in PipedriveConnector().accounts("token")]
    assert names == ["First page", "Second page"], (
        "the walk stopped before the second page")
    assert seen == [0, 1], f"expected two requests at offsets 0 and 1, made {seen}"


def test_pipedrive_stops_when_the_offset_does_not_advance(monkeypatch):
    """The other side of the same guard: a server that keeps handing back an
    offset at or below the current one is a loop, not a page."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={
            "data": [{"id": calls["n"], "name": f"Row {calls['n']}"}],
            "additional_data": {"pagination": {"more_items_in_collection": True,
                                               "next_start": 0}}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    import runtime.connectors.http as http_module
    original = http_module.request
    monkeypatch.setattr("runtime.connectors.pipedrive.request",
                        lambda method, url, **kw: original(method, url, client=client, **kw))

    rows = list(PipedriveConnector().accounts("token"))
    assert len(rows) == 1 and calls["n"] == 1, "an offset that never advances looped"


# -- salesforce.py:152  `or` -> `and` ------------------------------------

def test_salesforce_contacts_carry_their_mailing_country(monkeypatch):
    """`MailingCountry or None` became `and None`, so every contact arrived
    with no country at all.

    Country is not decoration here: `zolts/policy.py` resolves the jurisdiction
    from it, and a contact with none falls back to the account's or to `ZZ`. A
    Spanish contact silently losing `ES` is a send judged under the wrong
    country's rules — which is the one thing the policy engine exists to get
    right. The fixture has carried `MailingCountry: "ES"` all along; nothing
    asserted it reached the record.
    """
    client = httpx.Client(transport=httpx.MockTransport(_salesforce_handler))
    import runtime.connectors.http as http_module
    original = http_module.request
    monkeypatch.setattr("runtime.connectors.salesforce.request",
                        lambda method, url, **kw: original(method, url, client=client, **kw))

    contacts = list(SalesforceConnector.from_config(
        {"instance_url": SALESFORCE_INSTANCE}).contacts("token"))
    assert contacts, "the fixture yielded no contacts"
    countries = {c.country for c in contacts}
    assert countries != {None}, "every contact lost its country"
    assert "ES" in countries, f"the Spanish contact's country did not arrive: {countries}"


# -- watch.py:56  `0` -> `1` ---------------------------------------------

def test_a_watch_result_starts_at_zero_on_every_counter():
    """`stale: int = 0` became `= 1`, so a pass that refused nothing reported
    one refusal.

    Four of the five `Watched(...)` call sites name only the fields they have
    — the error paths name `signal_key` and `error` and nothing else — so the
    defaults are what the console reads. `stale` drives the amber "N stale
    refused" tile in the Signals view: under the mutation an operator is shown
    a warning about a signal nobody refused, on every clean pass.
    """
    empty = watch.Watched(signal_key="product.limit_hit")
    assert (empty.checked, empty.detected, empty.stale,
            empty.enrolled, empty.credits) == (0, 0, 0, 0, 0.0), (
        "a pass that did nothing does not report having done something")

    failed = watch.Watched(signal_key="product.limit_hit", error="boom")
    assert failed.stale == 0 and failed.detected == 0, (
        "a signal that errored reports no work, not one unit of it")


# -- dataprovider.py:104  `or` -> `and` ----------------------------------

def test_an_unknown_provider_is_refused_by_naming_the_known_ones():
    """`", ".join(...) or "none"` became `and "none"`, so the message said
    `registered: none` while providers were registered.

    The list exists for the operator who typed the name wrong: it is the
    difference between fixing a typo and going to debug registration for an
    hour. A message that lies is read at exactly the moment nobody can afford
    to be misled by it.
    """
    class _Stub:
        capabilities = type("C", (), {"provider": "survivor-probe"})()

    dataprovider.register_provider(_Stub())
    try:
        with pytest.raises(LookupError) as raised:
            dataprovider.get_provider("no-such-provider")
        message = str(raised.value)
        assert "survivor-probe" in message, (
            f"the refusal does not name what is registered: {message}")
        assert "registered: none" not in message
    finally:
        dataprovider._SOURCES.pop("survivor-probe", None)


def test_with_nothing_registered_the_refusal_says_none():
    """The fallback the `or` is there for, kept honest from the other side."""
    saved = dict(dataprovider._SOURCES)
    dataprovider._SOURCES.clear()
    try:
        with pytest.raises(LookupError, match="registered: none"):
            dataprovider.get_provider("anything")
    finally:
        dataprovider._SOURCES.update(saved)


# -- declarative_provider.py:169  `0` -> `1` -----------------------------

# The shipped document declares `unit_cost_micros`. This one deliberately does
# not, which is the only case the default is read in.
_SPEC_WITHOUT_A_PRICE = {
    "apiVersion": "zolts/v1", "kind": "DataProvider",
    "metadata": {"provider": "priceless"},
    "spec": {
        "transport": {"base_url": "https://example.test",
                      "auth": {"kind": "query", "name": "key"}},
        "billed_on_miss": True,
        "lookups": {
            "email": {"path": "/find", "method": "GET",
                      "query": {"domain": "account.domain"},
                      "response": {"email": "data.email"}},
        },
    },
}


def test_a_provider_that_declares_no_price_costs_nothing_on_a_miss():
    """`unit_cost_micros` defaulted to 0 and the mutation made it 1.

    The waterfall orders providers by price, and `_miss` is the path where a
    provider that bills for finding nothing still charges. A phantom micro on
    a provider that declares no cost is small, and it is in the direction that
    makes the optimiser's own COGS a fiction rather than a measurement.
    """
    provider = DeclarativeDataProvider(_SPEC_WITHOUT_A_PRICE)
    missed = provider._miss(reason="probe")
    assert missed.hit is False
    assert missed.cost_micros == 0, (
        "a provider with no declared unit cost invented one")


# -- worker.py:413  `!=` -> `==` -----------------------------------------

@requires_db
def test_the_heartbeat_carries_the_tick_and_not_its_errors(db):
    """`if k != "errors"` became `== "errors"`, so the heartbeat detail would
    have carried the errors and nothing else.

    `docs/25` sends an operator to that row and tells them what to read: *a
    fresh row every tick with `claimed: 0`*. Under the mutation the field the
    runbook names is simply absent, and the page that exists for three in the
    morning answers a different question than the one it was written for. The
    exclusion is deliberate — an error list is unbounded and this column is
    read by a human, not parsed.
    """
    from runtime.engine.worker import Worker

    with db.admin_tx() as cur:
        cur.execute("delete from worker_heartbeat")
    Worker(db, secret_key="k", dry_run=True, name="worker-detail").tick()

    with db.admin_tx() as cur:
        cur.execute("select detail from worker_heartbeat where name = 'worker-detail'")
        detail = (cur.fetchone() or {}).get("detail") or {}

    assert "claimed" in detail, (
        f"the runbook reads `claimed` from this row and it is not there: {sorted(detail)}")
    assert detail["claimed"] == 0, "an idle tick claimed nothing"
    assert "errors" not in detail, (
        "the error list is deliberately kept out of a column a human reads")


# -- cli.py:704  `is not` -> `is` ----------------------------------------

@requires_db
def test_the_command_line_reports_a_provider_as_authenticated_only_when_it_is(
        db, tenant, monkeypatch, capsys):
    """`row["connection_id"] is not None` became `is None`, so the command line
    printed the opposite of the truth.

    An operator running `zolts data-provider` to find out whether a supplier is
    connected is told it is when it is not, and told it is not when it is. The
    failure mode is the expensive direction: a provider reported as
    authenticated is one nobody goes and connects, and every lookup through it
    errors until somebody notices.

    Both sides are asserted. A one-sided check is what let a mutation live in
    the interval reader (D-57), and the same shape would let this one live.
    """
    import json as jsonlib

    from runtime import cli

    tid = str(tenant["id"])
    monkeypatch.setenv("ZOLTS_DATABASE_URL", OWNER_URL or "")
    monkeypatch.setenv("ZOLTS_APP_DATABASE_URL", APP_URL or "")
    monkeypatch.setenv("ZOLTS_SECRET_KEY", "cli-test-secret-key")

    def show(*extra: str) -> dict:
        capsys.readouterr()
        code = cli.main(["data-provider", "--tenant", tid, "--key", "probe-provider",
                         "--fields", "email", "--cost-micros", "1000", *extra])
        assert code == 0
        return jsonlib.loads(capsys.readouterr().out)

    unconnected = show()
    assert unconnected["authenticated"] is False, (
        "a provider with no stored credential is reported as authenticated")

    with db.tenant_tx(tid) as cur:
        cur.execute(
            "insert into connection (tenant_id, provider, display_name, secret_enc)"
            " values (%s,'probe-provider','probe',%s) returning id",
            (tid, b"not-a-real-credential"))
        connection_id = str(cur.fetchone()["id"])

    connected = show("--connection", connection_id)
    assert connected["authenticated"] is True, (
        "a provider holding a sealed credential is reported as unauthenticated")
