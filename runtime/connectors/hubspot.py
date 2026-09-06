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
from runtime.connectors.http import body, request

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

    # -- inbound ---------------------------------------------------------

    def companies(self, token: str, *, limit: int = 100) -> Iterator[dict[str, Any]]:
        yield from self._paged(token, "companies",
                               ["name", "domain", "country", "numberofemployees", "industry"],
                               limit)

    def contacts(self, token: str, *, limit: int = 100) -> Iterator[dict[str, Any]]:
        yield from self._paged(token, "contacts",
                               ["email", "firstname", "lastname", "jobtitle", "country",
                                "hs_email_optout", "associatedcompanyid"], limit)

    def _paged(self, token: str, object_type: str, properties: list[str],
               limit: int) -> Iterator[dict[str, Any]]:
        after: str | None = None
        while True:
            params: dict[str, Any] = {"limit": limit, "properties": ",".join(properties)}
            if after:
                params["after"] = after
            payload = body(request("GET", f"{self.base}/crm/v3/objects/{object_type}",
                                   token=token, params=params))
            yield from payload.get("results") or []
            after = ((payload.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return
