"""The CRM contract suite.

ADR-006: every connector implements the same interface and passes this suite in
CI. It is parametrised over every registered source, so adding a CRM adds its
rows here automatically and a connector cannot ship without answering these
questions.

The suite tests the connector's *mapping*, not the provider's API. Each source
is driven by a recorded payload in the provider's real shape — HubSpot's nested
`properties`, Pipedrive's email arrays and `org_id` objects — because the thing
that breaks when a CRM is added is the mapping, and the thing that would make
this suite worthless is writing the fixtures in the canonical shape.
"""

from __future__ import annotations

import json

import httpx
import pytest

from runtime.connectors import crm
from runtime.connectors.crm import Capabilities, Consent, CrmAccount, CrmContact
from runtime.connectors.generic import GenericSource
from runtime.connectors.hubspot import HubSpotConnector
from runtime.connectors.pipedrive import PipedriveConnector

# One recorded page per provider, in the provider's own shape.
HUBSPOT_PAGES = {
    "companies": {"results": [
        {"id": "701", "properties": {"name": "Northwind", "domain": "northwind.test",
                                     "country": "ES", "numberofemployees": "120",
                                     "industry": "LOGISTICS"}},
        {"id": "702", "properties": {"domain": "kestrel.test"}},
        {"id": "703", "properties": {}},                       # no name, no domain
    ]},
    "contacts": {"results": [
        {"id": "9001", "properties": {"email": "dana@northwind.test", "firstname": "Dana",
                                      "lastname": "Cruz", "jobtitle": "RevOps Lead",
                                      "country": "ES", "associatedcompanyid": "701"}},
        {"id": "9002", "properties": {"email": "gone@northwind.test",
                                      "hs_email_optout": "true"}},
        {"id": "9003", "properties": {"firstname": "No Email"}},
    ]},
}

PIPEDRIVE_PAGES = {
    "organizations": {"data": [
        {"id": 11, "name": "Northwind", "address_country": "ES", "people_count": 120},
        {"id": 12, "name": None},                              # unnamed
    ], "additional_data": {"pagination": {"more_items_in_collection": False}}},
    "persons": {"data": [
        {"id": 21, "name": "Dana Cruz", "job_title": "RevOps Lead",
         "email": [{"value": "old@northwind.test", "primary": False},
                   {"value": "dana@northwind.test", "primary": True}],
         "org_id": {"value": 11, "name": "Northwind"}, "marketing_status": "subscribed"},
        {"id": 22, "name": "Gone", "email": [{"value": "gone@northwind.test", "primary": True}],
         "marketing_status": "unsubscribed"},
        {"id": 23, "name": "Never Asked",
         "email": [{"value": "quiet@northwind.test", "primary": True}],
         "marketing_status": "no_consent"},
        {"id": 24, "name": "No Email", "email": []},
    ], "additional_data": {"pagination": {"more_items_in_collection": False}}},
}


def _hubspot_handler(request: httpx.Request) -> httpx.Response:
    for resource, payload in HUBSPOT_PAGES.items():
        if request.url.path.endswith(f"/objects/{resource}"):
            return httpx.Response(200, json=payload)
    return httpx.Response(200, json={"results": []})


def _pipedrive_handler(request: httpx.Request) -> httpx.Response:
    for resource, payload in PIPEDRIVE_PAGES.items():
        if request.url.path.endswith(f"/{resource}"):
            return httpx.Response(200, json=payload)
    return httpx.Response(200, json={"data": []})


# A tenant's own CRM, defined by a document rather than by code. It is in this
# suite for the reason it exists: a mapping is not a lesser integration with a
# separate code path, it is a source that answers the same questions.
IN_HOUSE_MAPPING = {
    "apiVersion": "zolts/v1", "kind": "CrmMapping",
    "metadata": {"provider": "acme-internal", "name": "Acme internal CRM"},
    "spec": {
        "transport": {
            "kind": "http", "base_url": "https://crm.acme.internal/api",
            "auth": {"kind": "header", "name": "x-acme-key"},
            "accounts": {"path": "/companies", "records": "data.items",
                         "pagination": {"kind": "none"}},
            "contacts": {"path": "/people", "records": "data.items",
                         "pagination": {"kind": "none"}},
        },
        "accounts": {"external_id": "id | str", "name": "legal_name",
                     "domain": "website | domain",
                     "country": "address.country_code | upper"},
        "contacts": {"external_id": "id | str",
                     "email": "emails[primary].address | trim | lower",
                     "full_name": "name_parts | join", "title": "role",
                     "account_external_id": "company.id | str",
                     "consent": {"field": "mail_pref",
                                 "values": {"opted_in": "allowed",
                                            "opted_out": "opted_out",
                                            "never_asked": "unknown"},
                                 "default": "unknown"}},
    },
}

IN_HOUSE_PAGES = {
    "companies": {"data": {"items": [
        {"id": 11, "legal_name": "Northwind SL", "website": "https://www.northwind.test/",
         "address": {"country_code": "es"}},
        {"id": 12, "legal_name": None},                        # unnamed
    ]}},
    "people": {"data": {"items": [
        {"id": 21, "name_parts": ["Dana", "Cruz"], "role": "RevOps Lead",
         "emails": [{"address": "old@northwind.test"},
                    {"address": " DANA@NORTHWIND.TEST ", "primary": True}],
         "company": {"id": 11}, "mail_pref": "opted_in"},
        {"id": 22, "name_parts": ["Gone"], "mail_pref": "opted_out",
         "emails": [{"address": "gone@northwind.test", "primary": True}]},
        {"id": 23, "name_parts": ["Never Asked"], "mail_pref": "never_asked",
         "emails": [{"address": "quiet@northwind.test", "primary": True}]},
        {"id": 24, "name_parts": ["No Email"], "emails": []},
    ]}},
}


def _in_house_handler(request: httpx.Request) -> httpx.Response:
    for resource, payload in IN_HOUSE_PAGES.items():
        if request.url.path.endswith(f"/{resource}"):
            return httpx.Response(200, json=payload)
    return httpx.Response(200, json={"data": {"items": []}})


SOURCES = {
    "hubspot": (HubSpotConnector, _hubspot_handler),
    "pipedrive": (PipedriveConnector, _pipedrive_handler),
    "in-house-mapping": (lambda: GenericSource(IN_HOUSE_MAPPING), _in_house_handler),
}


@pytest.fixture(params=sorted(SOURCES), ids=sorted(SOURCES))
def source(request, monkeypatch):
    factory, handler = SOURCES[request.param]
    client = httpx.Client(transport=httpx.MockTransport(handler))
    import runtime.connectors.http as http_module

    original = http_module.request

    def routed(method, url, **kwargs):
        return original(method, url, client=client, **kwargs)

    monkeypatch.setattr("runtime.connectors.hubspot.request", routed)
    monkeypatch.setattr("runtime.connectors.pipedrive.request", routed)
    monkeypatch.setattr("runtime.connectors.generic.request", routed)
    return factory()


# -- the contract itself --------------------------------------------------

def test_it_declares_capabilities(source):
    caps = source.capabilities
    assert isinstance(caps, Capabilities)
    assert caps.provider and caps.page_size > 0


def test_it_satisfies_the_protocol(source):
    assert isinstance(source, crm.CrmSource)


def test_accounts_are_canonical_records_with_a_stable_id(source):
    accounts = list(source.accounts("credential"))
    assert accounts, "a source that reads accounts must return some"
    assert all(isinstance(a, CrmAccount) for a in accounts)
    ids = [a.external_id for a in accounts]
    assert all(ids) and len(ids) == len(set(ids)), "ids must exist and be unique"
    assert all(a.name for a in accounts), "a nameless account cannot be upserted"


def test_contacts_are_canonical_records_and_always_carry_an_email(source):
    contacts = list(source.contacts("credential"))
    assert contacts
    assert all(isinstance(c, CrmContact) for c in contacts)
    assert all(c.email for c in contacts), (
        "a contact with no address is not reachable; drop it in the connector "
        "rather than making every caller check")


def test_a_record_the_provider_cannot_map_is_dropped_not_half_built(source):
    """Each fixture contains a company with no name and a person with no email.
    Neither may reach the canonical entities as a partial row."""
    names = [a.name for a in source.accounts("credential")]
    emails = [c.email for c in source.contacts("credential")]
    assert None not in names and "" not in names
    assert None not in emails and "" not in emails


def test_account_links_resolve_to_ids_this_source_also_returns(source):
    """A contact pointing at a company the account pass never yielded produces
    an orphan that silently never gets linked."""
    account_ids = {a.external_id for a in source.accounts("credential")}
    linked = {c.account_external_id for c in source.contacts("credential")
              if c.account_external_id}
    assert linked <= account_ids, f"orphan account references: {linked - account_ids}"


# -- consent: the field that decides whether a connector is a liability ---

def test_consent_is_one_of_the_three_states(source):
    assert all(c.consent in set(Consent) for c in source.contacts("credential"))


def test_a_source_claiming_to_read_opt_out_actually_returns_one(source):
    """A false claim here is worse than an honest gap: the gap is handled by
    the policy engine, the claim is trusted by it."""
    if not source.capabilities.reads_opt_out:
        pytest.skip(f"{source.capabilities.provider} does not claim to read opt-out")
    states = {c.consent for c in source.contacts("credential")}
    assert Consent.OPTED_OUT in states, (
        "it claims reads_opt_out but never reported one, on a fixture that "
        "contains an unsubscribed contact")


def test_never_asked_is_not_permission(source):
    """The distinction the whole field exists for.

    Pipedrive's `no_consent` means nobody ever asked. Flattening it to a
    boolean would turn that into a yes.
    """
    by_email = {c.email: c.consent for c in source.contacts("credential")}
    quiet = by_email.get("quiet@northwind.test")
    if quiet is None:
        pytest.skip("this fixture has no never-asked contact")
    assert quiet is Consent.UNKNOWN


def test_unknown_consent_never_produces_a_legal_basis():
    """The rule that keeps a CRM's silence from becoming permission."""
    unknown = CrmContact(external_id="1", email="x@y.test", consent=Consent.UNKNOWN)
    state = crm.consent_state(unknown, "somecrm")
    assert state["email"]["basis"] == "unknown"
    assert "basis" not in json.dumps(state).replace('"basis": "unknown"', ""), (
        "no legal basis may be manufactured from an absent field")


def test_an_opted_out_contact_produces_an_opt_out_not_a_basis():
    out = CrmContact(external_id="1", email="x@y.test", consent=Consent.OPTED_OUT)
    assert crm.consent_state(out, "somecrm")["email"]["opted_out"] is True


# -- pagination -----------------------------------------------------------

def test_pagination_terminates(source):
    """Pipedrive returns `more_items_in_collection: true` with a null
    `next_start` on the last page. Trusting the flag alone loops forever."""
    assert len(list(source.accounts("credential"))) < 1000
    assert len(list(source.contacts("credential"))) < 1000


def test_a_source_is_re_readable(source):
    """The sync walks accounts and contacts in separate passes; a generator
    that can only be consumed once would silently produce an empty second pass."""
    assert list(source.accounts("credential")) == list(source.accounts("credential"))


# -- a mapping is a first-class source ------------------------------------

def test_a_mapping_applies_its_transforms(source):
    """The in-house fixture stores an address as ' DANA@NORTHWIND.TEST ' and a
    name as two parts. What reaches the runtime is neither."""
    if source.capabilities.provider != "acme-internal":
        pytest.skip("mapping-specific")
    contacts = {c.email: c for c in source.contacts("credential")}
    assert "dana@northwind.test" in contacts
    assert contacts["dana@northwind.test"].full_name == "Dana Cruz"
    accounts = list(source.accounts("credential"))
    assert accounts[0].domain == "northwind.test", "a URL became a domain"
    assert accounts[0].country == "ES", "a lowercase code became a country"


def test_a_mapping_with_no_consent_block_cannot_claim_to_read_opt_out():
    """Omitting the block is a statement, not an oversight."""
    silent = {**IN_HOUSE_MAPPING}
    silent["spec"] = {**IN_HOUSE_MAPPING["spec"]}
    silent["spec"]["contacts"] = {k: v for k, v in IN_HOUSE_MAPPING["spec"]["contacts"].items()
                                  if k != "consent"}
    source = GenericSource(silent)
    assert source.capabilities.reads_opt_out is False
    assert any("consent unknown" in c for c in source.capabilities.caveats)


def test_a_mapping_names_itself_as_tenant_authored():
    """An operator reading a sync report must be able to tell a connector this
    repository maintains from a document their own team wrote."""
    source = GenericSource(IN_HOUSE_MAPPING)
    assert any("tenant-authored" in c for c in source.capabilities.caveats)


def test_a_mapping_that_never_terminates_is_a_bug_not_a_big_portal(monkeypatch):
    """Offset pagination with a server that always returns a full page would
    otherwise walk forever."""
    from runtime.connectors.generic import MAX_PAGES

    endless = {**IN_HOUSE_MAPPING}
    endless["spec"] = {**IN_HOUSE_MAPPING["spec"]}
    endless["spec"]["transport"] = {**IN_HOUSE_MAPPING["spec"]["transport"]}
    endless["spec"]["transport"]["contacts"] = {
        "path": "/people", "records": "data.items",
        "pagination": {"kind": "offset", "param": "offset", "size": 4}}

    client = httpx.Client(transport=httpx.MockTransport(_in_house_handler))
    import runtime.connectors.http as http_module

    original = http_module.request
    monkeypatch.setattr("runtime.connectors.generic.request",
                        lambda m, u, **kw: original(m, u, client=client, **kw))

    from zolts.mapping import MappingError

    with pytest.raises(MappingError, match="without terminating"):
        list(GenericSource(endless).contacts("credential"))
    assert MAX_PAGES < 100_000, "the ceiling must be reachable in a test"
