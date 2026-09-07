"""Pipedrive.

The second CRM, and it exists to prove the seam is not HubSpot-shaped. Almost
nothing about its API matches: organizations rather than companies, an array of
email addresses per person rather than a field, a nested `org_id` object rather
than an id, offset pagination rather than a cursor, and the credential in the
query string rather than a bearer header.

If the contract survives that, adding a third CRM is a mapping exercise.

`marketing_status` is the reason this connector can claim `reads_opt_out`. It
is an enum rather than a boolean, and only two of its four values mean the
person may be emailed — `no_consent` is not the same as `unsubscribed`, and
neither is permission.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from runtime.connectors.crm import (Capabilities, Consent, CrmAccount, CrmContact,
                                    CrmOpportunity, DealStatus)
from runtime.connectors.http import body, request

BASE = "https://api.pipedrive.com/v1"

# Pipedrive's four marketing states. Only `subscribed` is permission; the other
# three are degrees of not-permission, and flattening them to a boolean would
# turn "never asked" into "said yes".
_CONSENT = {
    "subscribed": Consent.ALLOWED,
    "unsubscribed": Consent.OPTED_OUT,
    "archived": Consent.OPTED_OUT,
    "no_consent": Consent.UNKNOWN,
}


# Pipedrive names deal state exactly as this runtime does, with one extra:
# `deleted`. A deleted deal is not a lost deal — it is a record the tenant
# withdrew — and counting it as lost would let an account back into outbound
# on the strength of a deal nobody closed. It is skipped instead.
_DEAL_STATUS = {
    "open": DealStatus.OPEN,
    "won": DealStatus.WON,
    "lost": DealStatus.LOST,
}


@dataclass
class PipedriveConnector:
    provider: str = "pipedrive"
    channels: frozenset[str] = frozenset({"task", "crm"})
    base: str = BASE

    capabilities = Capabilities(
        provider="pipedrive", reads_accounts=True, reads_contacts=True,
        reads_opt_out=True, reads_opportunities=True, writes_tasks=False, page_size=100,
        caveats=("`no_consent` is reported as unknown, not as permission",
                 "task creation is not implemented; a play's task step will "
                 "fail loudly rather than silently skip"))

    # -- reads -----------------------------------------------------------

    def accounts(self, credential: str) -> Iterator[CrmAccount]:
        for org in self._paged(credential, "organizations"):
            name = org.get("name")
            if not name:
                continue
            yield CrmAccount(
                external_id=str(org.get("id")), name=name,
                # Pipedrive has no domain field; the closest honest answer is
                # nothing, and inventing one from the name would create
                # accounts that collide with real domains on upsert.
                domain=None,
                country=(org.get("address_country") or None),
                attributes={"people_count": org.get("people_count")})

    def contacts(self, credential: str) -> Iterator[CrmContact]:
        for person in self._paged(credential, "persons"):
            email = _primary_email(person.get("email"))
            if not email:
                continue
            org = person.get("org_id")
            org_id = org.get("value") if isinstance(org, dict) else org
            yield CrmContact(
                external_id=str(person.get("id")), email=email,
                full_name=person.get("name") or None,
                title=(person.get("job_title") or None),
                account_external_id=str(org_id) if org_id else None,
                consent=_CONSENT.get(str(person.get("marketing_status") or "").lower(),
                                     Consent.UNKNOWN))

    def opportunities(self, credential: str) -> Iterator[CrmOpportunity]:
        for deal in self._paged(credential, "deals"):
            state = str(deal.get("status") or "").lower()
            if state not in _DEAL_STATUS:
                # `deleted`, or a state a future Pipedrive adds. Skipped rather
                # than defaulted: the caller distinguishes "no deals" from
                # "cannot read deals", and neither is "a deal I misfiled".
                continue
            org = deal.get("org_id")
            org_id = org.get("value") if isinstance(org, dict) else org
            yield CrmOpportunity(
                external_id=str(deal.get("id")),
                account_external_id=str(org_id) if org_id else None,
                name=deal.get("title") or None,
                stage=(str(deal["stage_id"]) if deal.get("stage_id") is not None else None),
                status=_DEAL_STATUS[state],
                amount_micros=_micros(deal.get("value")),
                currency=deal.get("currency") or None,
                owner=((deal.get("user_id") or {}).get("name")
                       if isinstance(deal.get("user_id"), dict) else None),
                opened_at=deal.get("add_time"),
                closed_at=deal.get("close_time"))

    # -- transport -------------------------------------------------------

    def _paged(self, credential: str, resource: str) -> Iterator[dict[str, Any]]:
        start = 0
        while True:
            payload = body(request("GET", f"{self.base}/{resource}",
                                   params={"api_token": credential, "start": start,
                                           "limit": self.capabilities.page_size}))
            for item in payload.get("data") or []:
                yield item
            pagination = ((payload.get("additional_data") or {}).get("pagination") or {})
            if not pagination.get("more_items_in_collection"):
                return
            # Pipedrive returns the next offset rather than a cursor, and
            # returns it as null on the final page even when the flag says
            # otherwise. Trusting the flag alone would loop forever.
            next_start = pagination.get("next_start")
            if next_start is None or next_start <= start:
                return
            start = next_start


def _primary_email(emails: Any) -> str | None:
    """Pipedrive stores several addresses per person, one flagged primary."""
    if isinstance(emails, str):
        return emails or None
    if not isinstance(emails, list) or not emails:
        return None
    primary = next((e for e in emails if isinstance(e, dict) and e.get("primary")), None)
    chosen = primary or next((e for e in emails if isinstance(e, dict)), None)
    return (chosen or {}).get("value") or None


def _micros(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(round(float(value) * 1_000_000))
    except (TypeError, ValueError):
        return None
