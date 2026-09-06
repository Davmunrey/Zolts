"""Pulling a CRM into the canonical entities.

Provider-agnostic. It takes a `CrmSource` and knows nothing about whose CRM it
is reading — the previous version had eleven HubSpot field names compiled into
it, which made "connect a second CRM" mean "write a second sync".

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
from dataclasses import dataclass, field
from typing import Any

from runtime.connectors.crm import Consent, CrmSource, consent_state, get_source
from runtime.db import Database
from runtime.repo import entities, ledger


@dataclass
class SyncReport:
    provider: str = ""
    accounts: int = 0
    people: int = 0
    links: int = 0
    skipped: int = 0
    # Contacts whose CRM could not say whether they had opted out. They are
    # stored, and they are unreachable until somebody establishes a basis.
    consent_unknown: int = 0
    opted_out: int = 0
    caveats: list[str] = field(default_factory=list)


def _batched(items: Iterable[Any], size: int) -> Iterator[list[Any]]:
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _sync_accounts(db: Database, tenant_id: str, source: CrmSource, credential: str,
                   report: SyncReport, batch_size: int) -> dict[str, str]:
    external_to_id: dict[str, str] = {}
    for page in _batched(source.accounts(credential), batch_size):
        with db.tenant_tx(tenant_id) as cur:
            for account in page:
                row = entities.upsert_account(
                    cur, tenant_id, name=account.name, domain=account.domain,
                    country=account.country, employee_band=account.employee_band,
                    industry_code=account.industry,
                    crm_id=f"{report.provider}:{account.external_id}",
                    attributes=account.attributes)
                external_to_id[account.external_id] = str(row["id"])
                report.accounts += 1
    return external_to_id


def _sync_contacts(db: Database, tenant_id: str, source: CrmSource, credential: str,
                   report: SyncReport, external_to_id: dict[str, str],
                   batch_size: int) -> None:
    provider = report.provider
    for page in _batched(source.contacts(credential), batch_size):
        with db.tenant_tx(tenant_id) as cur:
            for contact in page:
                if not contact.email:
                    report.skipped += 1
                    continue
                person = entities.upsert_person(
                    cur, tenant_id, email=contact.email, full_name=contact.full_name,
                    country=contact.country,
                    consent_state=consent_state(contact, provider),
                    crm_id=f"{provider}:{contact.external_id}",
                    attributes={"title": contact.title, **contact.attributes})

                if contact.consent is Consent.OPTED_OUT:
                    # One-way and recorded twice: consent state and the
                    # suppression list are read by different rules, and
                    # recording only one leaves a path that still allows.
                    entities.suppress(cur, tenant_id, "email", str(contact.email),
                                      "crm_opt_out", provider)
                    report.opted_out += 1
                elif contact.consent is Consent.UNKNOWN:
                    report.consent_unknown += 1

                report.people += 1
                account_id = external_to_id.get(contact.account_external_id or "")
                if account_id:
                    entities.link(cur, tenant_id, str(person["id"]), account_id,
                                  title=contact.title)
                    report.links += 1


def pull_staged(db: Database, tenant_id: str, source: CrmSource,
                batch_size: int = 200) -> SyncReport:
    """Records already in hand rather than fetched.

    A CRM behind a firewall posts batches; everything downstream is identical,
    which is the whole reason the transport is not part of the contract.
    """
    return pull(db, tenant_id, credential="", source=source, batch_size=batch_size)


def pull(db: Database, tenant_id: str, credential: str, *, provider: str = "hubspot",
         source: CrmSource | None = None, batch_size: int = 200) -> SyncReport:
    """Read a CRM into the canonical entities.

    The provider is named once. Everything after it is the contract.
    """
    source = source or get_source(provider)
    report = SyncReport(provider=source.capabilities.provider,
                        caveats=list(source.capabilities.caveats))
    if not source.capabilities.reads_opt_out:
        # Said once, loudly, in the report an operator reads. A CRM that cannot
        # answer this does not get its silence read as permission.
        report.caveats.insert(
            0, f"{report.provider} does not expose opt-out state: every contact is "
               "stored with consent unknown and is unreachable until a basis is "
               "established elsewhere")

    external_to_id = _sync_accounts(db, tenant_id, source, credential, report, batch_size)
    _sync_contacts(db, tenant_id, source, credential, report, external_to_id, batch_size)

    with db.tenant_tx(tenant_id) as cur:
        ledger.audit(cur, tenant_id, actor=f"sync:{report.provider}",
                     action="crm.sync", subject=None,
                     detail={"accounts": report.accounts, "people": report.people,
                             "links": report.links, "skipped": report.skipped,
                             "consent_unknown": report.consent_unknown,
                             "opted_out": report.opted_out})
    return report
