"""Pulling a CRM into the canonical entities.

A sync is not an import: it runs repeatedly, and the second run must produce no
new rows. Every write goes through the repository upserts, which key on domain
and email, so re-running is a no-op rather than a duplication.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime.connectors.hubspot import HubSpotConnector
from runtime.db import Database
from runtime.repo import entities, ledger


@dataclass
class SyncReport:
    accounts: int = 0
    people: int = 0
    links: int = 0
    skipped: int = 0


def _opted_out(properties: dict[str, Any]) -> bool:
    value = properties.get("hs_email_optout")
    return str(value).lower() in {"true", "yes", "1"}


def hubspot_to_entities(db: Database, tenant_id: str, token: str, *,
                        connector: HubSpotConnector | None = None,
                        limit: int = 100) -> SyncReport:
    connector = connector or HubSpotConnector()
    report = SyncReport()
    crm_to_account: dict[str, str] = {}

    with db.tenant_tx(tenant_id) as cur:
        for company in connector.companies(token, limit=limit):
            props = company.get("properties") or {}
            if not props.get("name") and not props.get("domain"):
                report.skipped += 1
                continue
            row = entities.upsert_account(
                cur, tenant_id, name=props.get("name") or props.get("domain"),
                domain=props.get("domain"), country=props.get("country"),
                industry_code=props.get("industry"), crm_id=str(company.get("id")),
                attributes={"employees": props.get("numberofemployees")})
            crm_to_account[str(company.get("id"))] = str(row["id"])
            report.accounts += 1

        for contact in connector.contacts(token, limit=limit):
            props = contact.get("properties") or {}
            email = props.get("email")
            if not email:
                report.skipped += 1
                continue
            name = " ".join(p for p in (props.get("firstname"), props.get("lastname")) if p)
            # An opt-out in the CRM is authoritative and one-way: it is recorded
            # as consent state and as a suppression, so neither the policy
            # engine nor a future sync can undo it by omission.
            consent = ({"email": {"opted_out": True, "source": "hubspot"}}
                       if _opted_out(props)
                       else {"email": {"basis": "legitimate_interest", "source": "hubspot"}})
            person = entities.upsert_person(
                cur, tenant_id, email=email, full_name=name or None,
                country=props.get("country"), consent_state=consent,
                crm_id=str(contact.get("id")),
                attributes={"title": props.get("jobtitle")})
            report.people += 1
            if _opted_out(props):
                entities.suppress(cur, tenant_id, "email", str(email),
                                  "crm_opt_out", "hubspot")

            account_id = crm_to_account.get(str(props.get("associatedcompanyid")))
            if account_id:
                entities.link(cur, tenant_id, str(person["id"]), account_id,
                              title=props.get("jobtitle"))
                report.links += 1

        ledger.audit(cur, tenant_id, actor="sync", action="hubspot.sync", subject=None,
                     detail={"accounts": report.accounts, "people": report.people,
                             "links": report.links, "skipped": report.skipped})
    return report
