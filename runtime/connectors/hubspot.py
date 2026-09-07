"""HubSpot CRM.

Two directions, both narrow on purpose:

* Outbound, as a connector: create the task or log the activity a play calls
  for, so a rep sees the work in the system they already live in.
* Inbound, as a sync: read companies and contacts into `account` and `person`
  so audiences resolve against real records rather than an import.

HubSpot has no idempotency-key header. The connector emulates one by writing
the key into a custom property and searching for it before creating anything,
which makes a redelivered action a lookup rather than a duplicate task.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from runtime.connectors.base import PermanentError, Request, Result
from runtime.connectors.crm import (Capabilities, Consent, CrmAccount, CrmContact,
                                    CrmOpportunity, DealStatus)
from runtime.connectors.http import body, request


def _truthy(value: Any) -> bool:
    return str(value).lower() in {"true", "yes", "1"}

BASE = "https://api.hubapi.com"

# Written on every object this runtime creates. Also the join key that makes
# redelivery safe, so it must exist in the portal before the first send; the
# connector creates it on demand rather than failing at dispatch time.
IDEMPOTENCY_PROPERTY = "zolts_idempotency_key"


@dataclass
class HubSpotConnector:
    provider: str = "hubspot"
    channels: frozenset[str] = frozenset({"task", "crm"})
    base: str = BASE
    _ensured: set[str] = field(default_factory=set)

    # -- outbound --------------------------------------------------------

    def execute(self, request_: Request) -> Result:
        if request_.secret is None:
            raise PermanentError("hubspot connection has no token")
        if request_.dry_run:
            return Result(ok=True, provider_ref="dry-run", detail={"dry_run": True})

        self._ensure_property(request_.secret)
        existing = self._find_by_key(request_.secret, request_.idempotency_key)
        if existing:
            return Result(ok=True, provider_ref=existing, cost_micros=0,
                          detail={"deduplicated": True})

        step = request_.step or {}
        contact = request_.contact or {}
        subject = step.get("subject") or f"Zolts: {step.get('step', 'follow up')}"
        notes = step.get("body") or self._default_note(step, contact)

        response = request(
            "POST", f"{self.base}/crm/v3/objects/tasks", token=request_.secret,
            json={"properties": {
                "hs_task_subject": subject,
                "hs_task_body": notes,
                "hs_task_status": "NOT_STARTED",
                "hs_task_priority": {"t1": "HIGH", "t2": "MEDIUM"}.get(step.get("tier"), "MEDIUM"),
                "hs_timestamp": step.get("due_at"),
                IDEMPOTENCY_PROPERTY: request_.idempotency_key,
            }})
        created = body(response)
        return Result(ok=True, provider_ref=str(created.get("id")), cost_micros=0,
                      detail={"object": "task"})

    def _default_note(self, step: dict[str, Any], contact: dict[str, Any]) -> str:
        who = contact.get("full_name") or contact.get("email") or "the contact"
        return (f"Queued by Zolts for step '{step.get('step')}'. Contact: {who}. "
                f"Channel: {step.get('channel', 'task')}.")

    def _ensure_property(self, token: str) -> None:
        if token in self._ensured:
            return
        response = request("GET", f"{self.base}/crm/v3/properties/tasks/{IDEMPOTENCY_PROPERTY}",
                           token=token, allow=frozenset({404}))
        if response.status_code == 404:
            request("POST", f"{self.base}/crm/v3/properties/tasks", token=token,
                    json={"name": IDEMPOTENCY_PROPERTY, "label": "Zolts idempotency key",
                          "type": "string", "fieldType": "text", "groupName": "task_information"})
        self._ensured.add(token)

    def _find_by_key(self, token: str, key: str) -> str | None:
        response = request(
            "POST", f"{self.base}/crm/v3/objects/tasks/search", token=token,
            json={"filterGroups": [{"filters": [
                {"propertyName": IDEMPOTENCY_PROPERTY, "operator": "EQ", "value": key}]}],
                "limit": 1})
        results = body(response).get("results") or []
        return str(results[0]["id"]) if results else None

    # -- inbound: the CRM contract ---------------------------------------

    capabilities = Capabilities(
        provider="hubspot", reads_accounts=True, reads_contacts=True,
        reads_opt_out=True, reads_opportunities=True, writes_tasks=True, page_size=100,
        caveats=("employee count is a raw number, not a band; it is stored as an "
                 "attribute rather than mapped to one",))

    def accounts(self, credential: str) -> Iterator[CrmAccount]:
        for company in self._paged(credential, "companies",
                                   ["name", "domain", "country", "numberofemployees",
                                    "industry"], self.capabilities.page_size):
            props = company.get("properties") or {}
            name = props.get("name") or props.get("domain")
            if not name:
                continue
            yield CrmAccount(
                external_id=str(company.get("id")), name=name,
                domain=props.get("domain"), country=props.get("country"),
                industry=props.get("industry"),
                attributes={"employees": props.get("numberofemployees")})

    def contacts(self, credential: str) -> Iterator[CrmContact]:
        for contact in self._paged(credential, "contacts",
                                   ["email", "firstname", "lastname", "jobtitle", "country",
                                    "hs_email_optout", "associatedcompanyid"],
                                   self.capabilities.page_size):
            props = contact.get("properties") or {}
            email = props.get("email")
            if not email:
                continue
            name = " ".join(p for p in (props.get("firstname"), props.get("lastname")) if p)
            company = props.get("associatedcompanyid")
            yield CrmContact(
                external_id=str(contact.get("id")), email=email,
                full_name=name or None, title=props.get("jobtitle"),
                country=props.get("country"),
                account_external_id=str(company) if company else None,
                consent=(Consent.OPTED_OUT if _truthy(props.get("hs_email_optout"))
                         else Consent.ALLOWED))

    def opportunities(self, credential: str) -> Iterator[CrmOpportunity]:
        """Deals, with the company they belong to.

        The association is requested explicitly: a deal's company is not a
        property, it is an association, and a connector that asked only for
        properties would return every deal with no account and exclude nobody.
        """
        for deal in self._paged(credential, "deals",
                                ["dealname", "dealstage", "amount", "closedate",
                                 "createdate", "hs_is_closed", "hs_is_closed_won",
                                 "hubspot_owner_id"],
                                self.capabilities.page_size, associations="companies"):
            props = deal.get("properties") or {}
            companies = (((deal.get("associations") or {}).get("companies") or {})
                         .get("results") or [])
            yield CrmOpportunity(
                external_id=str(deal.get("id")),
                account_external_id=str(companies[0]["id"]) if companies else None,
                name=props.get("dealname"),
                stage=props.get("dealstage"),
                status=_deal_status(props),
                amount_micros=_micros(props.get("amount")),
                owner=props.get("hubspot_owner_id"),
                opened_at=props.get("createdate"),
                closed_at=props.get("closedate"))

    def _paged(self, token: str, object_type: str, properties: list[str],
               limit: int, associations: str | None = None) -> Iterator[dict[str, Any]]:
        after: str | None = None
        while True:
            params: dict[str, Any] = {"limit": limit, "properties": ",".join(properties)}
            if associations:
                params["associations"] = associations
            if after:
                params["after"] = after
            payload = body(request("GET", f"{self.base}/crm/v3/objects/{object_type}",
                                   token=token, params=params))
            yield from payload.get("results") or []
            after = ((payload.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return


def _deal_status(props: dict[str, Any]) -> DealStatus:
    """HubSpot answers with two booleans and a stage name we do not own.

    Won is checked first: a won deal is also closed, and reading `hs_is_closed`
    on its own would file every win as a loss. A deal HubSpot cannot classify
    stays open, which is the direction that leaves the account alone.
    """
    if _truthy(props.get("hs_is_closed_won")):
        return DealStatus.WON
    if _truthy(props.get("hs_is_closed")):
        return DealStatus.LOST
    return DealStatus.OPEN


def _micros(amount: Any) -> int | None:
    if amount in (None, ""):
        return None
    try:
        return int(round(float(amount) * 1_000_000))
    except (TypeError, ValueError):
        return None
