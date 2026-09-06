"""Pulling a CRM into the canonical entities.

A sync is not an import: it runs repeatedly, and the second run must produce no
new rows. Every write goes through the repository upserts, which key on domain
and email, so re-running is a no-op rather than a duplication.

It commits in batches rather than in one transaction. A portal with fifty
thousand contacts would otherwise hold a single transaction open for minutes,
and a failure at the end would lose every row before it. Batching means a
partial sync leaves real rows behind — the right outcome for an operation whose
every write is idempotent anyway, and the reason this can be re-run after a
timeout instead of restarted.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
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


def _batched(items: Iterable[Any], size: int) -> Iterator[list[Any]]:
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _sync_companies(db: Database, tenant_id: str, connector: HubSpotConnector, token: str,
                    report: SyncReport, limit: int, batch_size: int) -> dict[str, str]:
    crm_to_account: dict[str, str] = {}
    for page in _batched(connector.companies(token, limit=limit), batch_size):
        with db.tenant_tx(tenant_id) as cur:
            for company in page:
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
    return crm_to_account


def _sync_contacts(db: Database, tenant_id: str, connector: HubSpotConnector, token: str,
                   report: SyncReport, crm_to_account: dict[str, str], limit: int,
                   batch_size: int) -> None:
    for page in _batched(connector.contacts(token, limit=limit), batch_size):
        with db.tenant_tx(tenant_id) as cur:
            for contact in page:
                props = contact.get("properties") or {}
                email = props.get("email")
                if not email:
                    report.skipped += 1
                    continue
                name = " ".join(p for p in (props.get("firstname"), props.get("lastname")) if p)
                # An opt-out in the CRM is authoritative and one-way. It is
                # recorded as consent state and as a suppression, because the
                # two are read by different rules and recording only one leaves
                # a path that still allows.
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


def hubspot_to_entities(db: Database, tenant_id: str, token: str, *,
                        connector: HubSpotConnector | None = None,
                        limit: int = 100, batch_size: int = 200) -> SyncReport:
    connector = connector or HubSpotConnector()
    report = SyncReport()
    crm_to_account = _sync_companies(db, tenant_id, connector, token, report, limit, batch_size)
    _sync_contacts(db, tenant_id, connector, token, report, crm_to_account, limit, batch_size)
    with db.tenant_tx(tenant_id) as cur:
        ledger.audit(cur, tenant_id, actor="sync", action="hubspot.sync", subject=None,
                     detail={"accounts": report.accounts, "people": report.people,
                             "links": report.links, "skipped": report.skipped})
    return report
