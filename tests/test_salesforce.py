"""Salesforce, and the three things it does not share with the first two CRMs.

The contract suite already drives this connector alongside HubSpot, Pipedrive
and a tenant-authored mapping. What is here is what only Salesforce does: a
base URL that belongs to the customer, paging by a URL the server hands back,
and an opt-out field that is a boolean.
"""

from __future__ import annotations

import httpx
import pytest

from runtime.connectors.base import PermanentError
from runtime.connectors.crm import Consent
from runtime.connectors.salesforce import SalesforceConnector
from tests.test_crm_contract import SALESFORCE_INSTANCE, _salesforce_handler


@pytest.fixture
def source(monkeypatch):
    client = httpx.Client(transport=httpx.MockTransport(_salesforce_handler))
    import runtime.connectors.http as http_module

    original = http_module.request
    monkeypatch.setattr("runtime.connectors.salesforce.request",
                        lambda method, url, **kw: original(method, url, client=client, **kw))
    return SalesforceConnector.from_config({"instance_url": SALESFORCE_INSTANCE})


# -- the base URL belongs to the customer --------------------------------

def test_an_instance_url_is_required_and_named():
    """HubSpot and Pipedrive have one host for everybody. Salesforce gives each
    org its own, so a credential alone cannot reach it."""
    with pytest.raises(PermanentError, match="instance_url"):
        SalesforceConnector.from_config({})
    with pytest.raises(PermanentError, match="instance_url"):
        SalesforceConnector.from_config(None)


def test_an_unconfigured_connector_refuses_rather_than_guessing_a_host():
    with pytest.raises(PermanentError, match="from_config"):
        list(SalesforceConnector().accounts("token"))


def test_a_trailing_slash_does_not_double_up():
    built = SalesforceConnector.from_config(
        {"instance_url": "https://acme.my.salesforce.com/"})
    assert built.instance_url == "https://acme.my.salesforce.com"


def test_the_api_version_is_pinned_and_overridable():
    """A floating version means a field this connector reads can disappear on
    Salesforce's release schedule rather than on ours."""
    assert SalesforceConnector.from_config(
        {"instance_url": SALESFORCE_INSTANCE}).api_version.startswith("v")
    assert SalesforceConnector.from_config(
        {"instance_url": SALESFORCE_INSTANCE, "api_version": "v61.0"}).api_version == "v61.0"


# -- paging is a URL the server returns ----------------------------------

def test_the_next_page_url_is_followed(source):
    """There is nothing to increment. A connector that ignored nextRecordsUrl
    and re-sent the first query would loop on page one forever."""
    names = [a.name for a in source.accounts("token")]
    assert "Second Page Ltd" in names, "the second page was never fetched"
    assert names == ["Northwind", "Second Page Ltd"]


def test_a_repeated_page_url_terminates(monkeypatch):
    """The one failure worth guarding: a server that hands back the same
    continuation forever."""
    same = {"done": False, "nextRecordsUrl": "/services/data/v59.0/query/loop",
            "records": [{"Id": "1", "Name": "Round"}]}
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=same)))
    import runtime.connectors.http as http_module

    original = http_module.request
    monkeypatch.setattr("runtime.connectors.salesforce.request",
                        lambda method, url, **kw: original(method, url, client=client, **kw))

    built = SalesforceConnector.from_config({"instance_url": SALESFORCE_INSTANCE})
    assert len(list(built.accounts("token"))) == 2, "paging did not terminate"


# -- a boolean opt-out ----------------------------------------------------

def test_true_is_an_opt_out_and_false_is_legitimate_interest(source):
    """`HasOptedOutOfEmail` cannot distinguish 'asked us to stop' from 'was
    never asked'. True is definite. False means the CRM was checked and carries
    no opt-out, which is legitimate interest in this model — not a claim that
    anybody consented, and not the bare-boolean guess that inverted consent in
    the tenant-authored mapping layer.
    """
    by_email = {c.email: c for c in source.contacts("token")}
    assert by_email["dana@northwind.test"].consent is Consent.ALLOWED
    assert by_email["gone@northwind.test"].consent is Consent.OPTED_OUT


def test_salesforce_cannot_report_never_asked_and_says_so(source):
    """The contract suite skips its never-asked row for this source. That is a
    property of the CRM rather than a gap in a fixture, so it is asserted here
    instead of passing silently."""
    assert not any(c.consent is Consent.UNKNOWN for c in source.contacts("token"))
    caveats = " ".join(source.capabilities.caveats)
    assert "boolean" in caveats
    assert "legitimate interest rather than consent" in caveats


def test_an_org_holding_opt_out_elsewhere_is_named_in_the_caveats(source):
    """Plenty of orgs keep it in a custom field or in Marketing Cloud. This
    connector does not read those, and an operator learns that from the sync
    report rather than from a support ticket."""
    assert any("custom field" in c for c in source.capabilities.caveats)


# -- the mapping ----------------------------------------------------------

def test_a_website_typed_any_way_becomes_a_domain(source):
    accounts = {a.name: a for a in source.accounts("token")}
    assert accounts["Northwind"].domain == "northwind.test"
    assert accounts["Second Page Ltd"].domain == "second.test"


def test_an_employee_count_stays_a_number(source):
    """Inventing a band from an integer is a mapping nobody agreed and a buyer
    would have to unpick. HubSpot does the same."""
    northwind = next(a for a in source.accounts("token") if a.name == "Northwind")
    assert northwind.attributes["employees"] == 120
    assert northwind.employee_band is None


def test_a_contact_with_no_email_is_skipped(source):
    assert all(c.email for c in source.contacts("token"))


def test_a_name_is_assembled_from_the_parts_that_exist(source):
    by_email = {c.email: c for c in source.contacts("token")}
    assert by_email["dana@northwind.test"].full_name == "Dana Cruz"
    assert by_email["gone@northwind.test"].full_name == "Gone"
