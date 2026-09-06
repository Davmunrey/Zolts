"""Connector behaviour, driven through a mock transport.

The network is stubbed; the connector is not. What is under test is the part
that is ours and that fails expensively: how a failure is classified, whether a
redelivered action produces a second task or a second email, and whether the
connector ever proceeds on a guess.
"""

from __future__ import annotations

import json

import httpx
import pytest

from runtime.connectors.base import PermanentError, Request, TransientError
from runtime.connectors.hubspot import IDEMPOTENCY_PROPERTY, HubSpotConnector
from runtime.connectors.http import request as http_request
from runtime.connectors.smartlead import SmartleadConnector


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _request(**overrides) -> Request:
    base = dict(tenant_id="t", idempotency_key="prog/enr/email_1", channel="email",
                step={"step": "email_1", "channel": "email"},
                entity={"id": "a", "type": "account"},
                contact={"email": "dana@example.com", "full_name": "Dana Cruz"},
                secret="token", config={}, dry_run=False)
    base.update(overrides)
    return Request(**base)


# -- failure classification ---------------------------------------------

@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 408])
def test_provider_wobble_is_transient(status):
    client = _client(lambda r: httpx.Response(status, text="busy"))
    with pytest.raises(TransientError):
        http_request("GET", "https://example.test/x", client=client)


@pytest.mark.parametrize("status", [400, 404, 422])
def test_a_bad_request_is_permanent(status):
    client = _client(lambda r: httpx.Response(status, text="nope"))
    with pytest.raises(PermanentError):
        http_request("POST", "https://example.test/x", client=client)


def test_an_expected_status_is_handed_back_to_the_caller():
    """Regression: probing for an absent resource must not raise.

    The HubSpot connector creates its idempotency property on demand. While a
    404 raised inside the transport helper, that branch was unreachable and the
    first dispatch against a fresh portal failed permanently.
    """
    client = _client(lambda r: httpx.Response(404, json={}))
    response = http_request("GET", "https://example.test/x", allow=frozenset({404}),
                            client=client)
    assert response.status_code == 404


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_credentials_are_permanent(status):
    """Hammering an invalid token is how an account gets locked."""
    client = _client(lambda r: httpx.Response(status, text="denied"))
    with pytest.raises(PermanentError, match="credentials rejected"):
        http_request("GET", "https://example.test/x", client=client)


def test_a_timeout_is_transient():
    def handler(request):
        raise httpx.ConnectTimeout("too slow", request=request)

    with pytest.raises(TransientError):
        http_request("GET", "https://example.test/x", client=_client(handler))


# -- HubSpot -------------------------------------------------------------

class _HubSpotStub:
    """Enough of the portal to exercise the idempotency emulation."""

    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}
        self.property_exists = True
        self.creates = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(f"/properties/tasks/{IDEMPOTENCY_PROPERTY}"):
            return httpx.Response(200 if self.property_exists else 404, json={})
        if path.endswith("/properties/tasks") and request.method == "POST":
            self.property_exists = True
            return httpx.Response(201, json={"name": IDEMPOTENCY_PROPERTY})
        if path.endswith("/tasks/search"):
            wanted = json.loads(request.content)["filterGroups"][0]["filters"][0]["value"]
            hit = [{"id": tid} for tid, t in self.tasks.items()
                   if t["properties"].get(IDEMPOTENCY_PROPERTY) == wanted]
            return httpx.Response(200, json={"results": hit[:1]})
        if path.endswith("/objects/tasks") and request.method == "POST":
            self.creates += 1
            body = json.loads(request.content)
            task_id = f"task-{self.creates}"
            self.tasks[task_id] = body
            return httpx.Response(201, json={"id": task_id})
        return httpx.Response(404, json={})


def _patched(monkeypatch, handler):
    client = _client(handler)
    import runtime.connectors.http as http_module

    original = http_module.request

    def routed(method, url, **kwargs):
        return original(method, url, client=client, **kwargs)


    monkeypatch.setattr("runtime.connectors.hubspot.request", routed)
    monkeypatch.setattr("runtime.connectors.smartlead.request", routed)
    return client


def test_hubspot_creates_a_task(monkeypatch):
    stub = _HubSpotStub()
    _patched(monkeypatch, stub)
    result = HubSpotConnector().execute(_request(channel="task"))
    assert result.ok and result.provider_ref == "task-1"
    assert stub.creates == 1


def test_hubspot_redelivery_does_not_create_a_second_task(monkeypatch):
    stub = _HubSpotStub()
    _patched(monkeypatch, stub)
    connector = HubSpotConnector()
    first = connector.execute(_request(channel="task"))
    second = connector.execute(_request(channel="task"))
    assert stub.creates == 1, "the emulated idempotency key must absorb the redelivery"
    assert second.detail.get("deduplicated") is True
    assert second.provider_ref == first.provider_ref


def test_hubspot_creates_the_idempotency_property_when_absent(monkeypatch):
    stub = _HubSpotStub()
    stub.property_exists = False
    _patched(monkeypatch, stub)
    HubSpotConnector().execute(_request(channel="task"))
    assert stub.property_exists, "a missing property must not fail at dispatch time"


def test_hubspot_refuses_without_a_token(monkeypatch):
    _patched(monkeypatch, _HubSpotStub())
    with pytest.raises(PermanentError, match="no token"):
        HubSpotConnector().execute(_request(channel="task", secret=None))


# -- Smartlead -----------------------------------------------------------

def test_smartlead_refuses_to_guess_a_campaign(monkeypatch):
    """Guessing a campaign means guessing whose reputation to spend."""
    _patched(monkeypatch, lambda r: httpx.Response(200, json={}))
    with pytest.raises(PermanentError, match="refusing to guess"):
        SmartleadConnector().execute(_request())


def test_smartlead_sends_and_reports_cost(monkeypatch):
    calls: list[httpx.Request] = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"upload_count": 1, "bulk_email_id": "be-1"})

    _patched(monkeypatch, handler)
    result = SmartleadConnector().execute(_request(config={"campaign_id": "c1"}))
    assert result.ok and result.cost_micros > 0, "sending must never be accounted as free"
    payload = json.loads(calls[0].content)
    assert payload["lead_list"][0]["custom_fields"]["zolts_idempotency_key"] == "prog/enr/email_1"


def test_smartlead_treats_an_already_added_lead_as_a_duplicate(monkeypatch):
    _patched(monkeypatch, lambda r: httpx.Response(
        200, json={"upload_count": 0, "already_added_to_campaign": 1}))
    result = SmartleadConnector().execute(_request(config={"campaign_id": "c1"}))
    assert result.ok and result.detail["deduplicated"] is True
    assert result.cost_micros == 0, "a deduplicated send costs nothing"


def test_smartlead_raises_when_nothing_was_accepted(monkeypatch):
    _patched(monkeypatch, lambda r: httpx.Response(
        200, json={"upload_count": 0, "already_added_to_campaign": 0}))
    with pytest.raises(PermanentError):
        SmartleadConnector().execute(_request(config={"campaign_id": "c1"}))


def test_smartlead_refuses_a_contact_with_no_address(monkeypatch):
    _patched(monkeypatch, lambda r: httpx.Response(200, json={}))
    with pytest.raises(PermanentError, match="no email address"):
        SmartleadConnector().execute(_request(config={"campaign_id": "c1"},
                                              contact={"full_name": "Dana"}))


def test_a_dry_run_reaches_no_provider(monkeypatch):
    def handler(request):
        raise AssertionError("a dry run must not call the provider")

    _patched(monkeypatch, handler)
    result = SmartleadConnector().execute(_request(config={"campaign_id": "c1"}, dry_run=True))
    assert result.ok and result.cost_micros == 0 and result.detail["dry_run"] is True


# -- pulling a CRM in ----------------------------------------------------

def test_a_sync_is_a_no_op_the_second_time(db, tenant, monkeypatch):
    """A sync is not an import: it runs repeatedly, and the second run must
    produce no new rows. It was written and never called until an audit found
    it had no caller — so this is the test that proves it works."""
    from runtime.connectors.sync import hubspot_to_entities

    class Stub:
        def companies(self, token, limit=100):
            yield {"id": "c1", "properties": {"name": "Northwind", "domain": "northwind.test",
                                              "country": "ES", "numberofemployees": "120"}}

        def contacts(self, token, limit=100):
            yield {"id": "p1", "properties": {"email": "dana@northwind.test",
                                              "firstname": "Dana", "lastname": "Cruz",
                                              "jobtitle": "RevOps Lead",
                                              "associatedcompanyid": "c1"}}

    tid = str(tenant["id"])
    first = hubspot_to_entities(db, tid, "token", connector=Stub())
    second = hubspot_to_entities(db, tid, "token", connector=Stub())
    assert first.accounts == 1 and first.people == 1 and first.links == 1
    with db.tenant_tx(tid) as cur:
        cur.execute("select count(*) as n from account")
        accounts = cur.fetchone()["n"]
        cur.execute("select count(*) as n from person")
        people = cur.fetchone()["n"]
    assert accounts == 1 and people == 1, "the second run must not duplicate"
    assert second.accounts == 1, "it still reports what it saw"


def test_a_crm_opt_out_becomes_a_suppression_not_just_a_flag(db, tenant):
    """One-way and recorded twice: consent state and the suppression list are
    consulted by different rules, and recording only one leaves a path that
    still allows."""
    from runtime.connectors.sync import hubspot_to_entities
    from runtime.repo import entities

    class Stub:
        def companies(self, token, limit=100):
            return iter(())

        def contacts(self, token, limit=100):
            yield {"id": "p9", "properties": {"email": "gone@northwind.test",
                                              "hs_email_optout": "true"}}

    tid = str(tenant["id"])
    hubspot_to_entities(db, tid, "token", connector=Stub())
    with db.tenant_tx(tid) as cur:
        assert entities.suppressed_keys(cur, "gone@northwind.test", None)
        cur.execute("select consent_state from person where email = %s",
                    ("gone@northwind.test",))
        assert cur.fetchone()["consent_state"]["email"]["opted_out"] is True


def test_a_sync_commits_per_batch_not_once_at_the_end(db, tenant):
    """A portal with fifty thousand contacts would otherwise hold one
    transaction open for minutes, and a failure at the end would lose every row
    before it. Found by wiring dead code up and asking what it does at scale."""
    from runtime.connectors.sync import hubspot_to_entities

    class Stub:
        def __init__(self):
            self.failed = False

        def companies(self, token, limit=100):
            for n in range(5):
                yield {"id": f"c{n}", "properties": {"name": f"Co {n}",
                                                     "domain": f"co{n}.test"}}

        def contacts(self, token, limit=100):
            yield {"id": "p0", "properties": {"email": "first@co0.test"}}
            # The provider dies half-way through the crawl.
            raise ConnectionError("the portal hung up")

    tid = str(tenant["id"])
    with pytest.raises(ConnectionError):
        hubspot_to_entities(db, tid, "token", connector=Stub(), batch_size=2)

    with db.tenant_tx(tid) as cur:
        cur.execute("select count(*) as n from account")
        accounts = cur.fetchone()["n"]
    assert accounts == 5, "the companies committed before the contacts failed"
