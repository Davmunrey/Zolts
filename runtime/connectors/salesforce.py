"""Salesforce.

The third CRM, and the one every enterprise buyer asks for. It is here to be
implemented against the contract rather than to change it — but it does expose
one thing the first two hid: **the base URL is per customer.** HubSpot and
Pipedrive have one host for everybody; Salesforce gives each org its own
`https://acme.my.salesforce.com`, so a credential alone is not enough to reach
it. That is why this connector is configured and the others are not.

Three more things about it differ, and each is a reason a third CRM was worth
writing rather than assuming:

* Records come from SOQL, not from resource paths. There is one endpoint and
  the query names the fields.
* Paging is a *URL the server returns*, not an offset or a cursor parameter.
  There is nothing to increment and nothing to guess.
* Employee count is an integer. It is stored as an attribute and declared as a
  caveat, exactly as HubSpot's is: inventing a band from a number is a mapping
  nobody agreed and a buyer would have to unpick.

**`HasOptedOutOfEmail` is why this connector may claim `reads_opt_out`,** and
the reason it is subtle enough to write down. It is a boolean, and a boolean
cannot distinguish "asked us to stop" from "was never asked". `true` is an
opt-out and is reported as one. `false` is reported as `ALLOWED`, and that is
correct in this model rather than a guess: `ALLOWED` here means legitimate
interest applies — that the CRM was checked and carries no opt-out — not that
anyone gave explicit consent. The distinction matters because reading a bare
boolean as permission is exactly the defect the tenant-authored mapping layer
shipped with, and it inverted consent for every positively-phrased field.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from runtime.connectors.base import PermanentError
from runtime.connectors.crm import Capabilities, Consent, CrmAccount, CrmContact
from runtime.connectors.http import body, request

# Pinned. Salesforce keeps old versions working for years, and a floating
# version means a field this connector reads can disappear on their release
# schedule rather than on ours.
API_VERSION = "v59.0"

ACCOUNT_SOQL = (
    "SELECT Id, Name, Website, BillingCountry, NumberOfEmployees, Industry "
    "FROM Account")
CONTACT_SOQL = (
    "SELECT Id, Email, FirstName, LastName, Title, MailingCountry, AccountId, "
    "HasOptedOutOfEmail FROM Contact")


@dataclass
class SalesforceConnector:
    """One Salesforce org. `instance_url` is which one."""

    instance_url: str | None = None
    api_version: str = API_VERSION
    provider: str = "salesforce"
    channels: frozenset[str] = frozenset({"task", "crm"})

    capabilities = Capabilities(
        provider="salesforce", reads_accounts=True, reads_contacts=True,
        reads_opt_out=True, writes_tasks=False, page_size=200,
        caveats=("employee count is a raw number, not a band; it is stored as an "
                 "attribute rather than mapped to one",
                 "HasOptedOutOfEmail is a boolean, so an opt-out is definite and "
                 "its absence is legitimate interest rather than consent",
                 "task creation is not implemented; a play's task step will fail "
                 "loudly rather than silently skip",
                 "orgs that hold opt-out in a custom field or in Marketing Cloud "
                 "are not read here; publish a mapping for those"))

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "SalesforceConnector":
        """Build one from a stored connection's config.

        The instance URL is the customer's, so it is configuration rather than
        a constant, and its absence is an error with a name rather than a
        request to the wrong host.
        """
        config = config or {}
        instance = (config.get("instance_url") or "").strip()
        if not instance:
            raise PermanentError(
                "salesforce needs the org's instance_url in the connection config, "
                "for example {\"instance_url\": \"https://acme.my.salesforce.com\"}; "
                "there is no shared host to fall back to")
        return cls(instance_url=instance.rstrip("/"),
                   api_version=(config.get("api_version") or API_VERSION))

    # -- reads -----------------------------------------------------------

    def accounts(self, credential: str) -> Iterator[CrmAccount]:
        for record in self._query(credential, ACCOUNT_SOQL):
            name = record.get("Name")
            if not name:
                continue
            yield CrmAccount(
                external_id=str(record.get("Id")), name=name,
                domain=_domain(record.get("Website")),
                country=(record.get("BillingCountry") or None),
                industry=(record.get("Industry") or None),
                attributes={"employees": record.get("NumberOfEmployees")})

    def contacts(self, credential: str) -> Iterator[CrmContact]:
        for record in self._query(credential, CONTACT_SOQL):
            email = (record.get("Email") or "").strip()
            if not email:
                continue
            account_id = record.get("AccountId")
            yield CrmContact(
                external_id=str(record.get("Id")), email=email,
                full_name=_full_name(record),
                title=(record.get("Title") or None),
                country=(record.get("MailingCountry") or None),
                account_external_id=str(account_id) if account_id else None,
                # True is an opt-out. False means the CRM was checked and
                # carries none, which is legitimate interest — see the module
                # docstring; it is not a claim that anybody consented.
                consent=(Consent.OPTED_OUT if record.get("HasOptedOutOfEmail")
                         else Consent.ALLOWED))

    # -- transport -------------------------------------------------------

    def _query(self, credential: str, soql: str) -> Iterator[dict[str, Any]]:
        if not self.instance_url:
            raise PermanentError(
                "this salesforce connector has no instance_url; build it with "
                "SalesforceConnector.from_config(connection.config)")

        url = f"{self.instance_url}/services/data/{self.api_version}/query"
        params: dict[str, Any] | None = {"q": soql}
        seen: set[str] = set()

        while True:
            payload = body(request("GET", url, token=credential, params=params))
            for record in payload.get("records") or []:
                yield record

            if payload.get("done") is not False:
                return
            # Salesforce hands back a path rather than a token to increment.
            # Nothing here is guessed; the only failure worth guarding is the
            # server repeating one, which would page forever.
            nxt = payload.get("nextRecordsUrl")
            if not nxt or nxt in seen:
                return
            seen.add(nxt)
            url = urljoin(self.instance_url + "/", nxt.lstrip("/"))
            params = None


def _full_name(record: dict[str, Any]) -> str | None:
    parts = [record.get("FirstName"), record.get("LastName")]
    joined = " ".join(str(p).strip() for p in parts if p and str(p).strip())
    return joined or None


def _domain(website: Any) -> str | None:
    """Salesforce stores Website however the rep typed it."""
    if not isinstance(website, str) or not website.strip():
        return None
    text = website.strip().lower()
    for scheme in ("https://", "http://"):
        if text.startswith(scheme):
            text = text[len(scheme):]
    text = text.split("/")[0].split("?")[0]
    if text.startswith("www."):
        text = text[4:]
    return text or None
