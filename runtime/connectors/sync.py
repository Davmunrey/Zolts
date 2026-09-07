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
    # Deals read, and how many of them are live. `open_deals` is the number
    # that will keep an account out of outbound, which is the figure an
    # operator actually wants from this report.
    opportunities: int = 0
    open_deals: int = 0
    orphan_deals: int = 0
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


def _sync_opportunities(db: Database, tenant_id: str, source: CrmSource, credential: str,
                        report: SyncReport, external_to_id: dict[str, str],
                        batch_size: int) -> None:
    """Deals, if this source has any to give.

    A source that cannot read them is not an error and not a zero: it is a
    source that was never asked, and `pull` records the difference in
    `crm_sync_state` so an audience can tell them apart.
    """
    provider = report.provider
    for page in _batched(source.opportunities(credential), batch_size):
        with db.tenant_tx(tenant_id) as cur:
            for deal in page:
                account_id = external_to_id.get(deal.account_external_id or "")
                if account_id is None:
                    # Kept, not dropped. A deal whose account this run did not
                    # see is still a deal; the count is reported so an
                    # operator can see a mapping that never resolves.
                    report.orphan_deals += 1
                entities.upsert_opportunity(
                    cur, tenant_id, provider=provider, crm_id=deal.external_id,
                    account_id=account_id, name=deal.name, stage=deal.stage,
                    status=deal.status.value, amount_micros=deal.amount_micros,
                    currency=deal.currency, owner=deal.owner,
                    opened_at=deal.opened_at, closed_at=deal.closed_at,
                    attributes=deal.attributes)
                report.opportunities += 1
                if deal.status.value == "open":
                    report.open_deals += 1


def _mark_opportunities_synced(db: Database, tenant_id: str, provider: str) -> None:
    """Record that this provider has now answered the deals question.

    An empty `opportunity` table means either "no open deals" or "nobody ever
    asked", and `not exists (...)` reads them identically. This row is what
    makes the difference queryable — see `runtime.engine.audience`.

    An upsert rather than an update of the connection row: a pushed batch from
    a firewalled CRM is the same sync with no credential to store, and a
    marker that silently fails to be written is a guard that silently stops
    guarding.
    """
    with db.tenant_tx(tenant_id) as cur:
        cur.execute(
            "insert into crm_sync_state (tenant_id, provider, opportunities_synced_at)"
            " values (%s,%s,now()) on conflict (tenant_id, provider) do update"
            "   set opportunities_synced_at = now(), updated_at = now()",
            (tenant_id, provider))


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

    if not source.capabilities.reads_opportunities:
        report.caveats.append(
            f"{report.provider} cannot say which accounts are already in a deal; a "
            "program whose audience excludes them refuses to enrol rather than "
            "contacting people the sales team is already talking to")

    external_to_id = _sync_accounts(db, tenant_id, source, credential, report, batch_size)
    _sync_contacts(db, tenant_id, source, credential, report, external_to_id, batch_size)
    if source.capabilities.reads_opportunities:
        _sync_opportunities(db, tenant_id, source, credential, report,
                            external_to_id, batch_size)
        _mark_opportunities_synced(db, tenant_id, report.provider)

    with db.tenant_tx(tenant_id) as cur:
        ledger.audit(cur, tenant_id, actor=f"sync:{report.provider}",
                     action="crm.sync", subject=None,
                     detail={"accounts": report.accounts, "people": report.people,
                             "links": report.links, "skipped": report.skipped,
                             "consent_unknown": report.consent_unknown,
                             "opted_out": report.opted_out,
                             "opportunities": report.opportunities,
                             "open_deals": report.open_deals,
                             "orphan_deals": report.orphan_deals,
                             "reads_opportunities":
                                 source.capabilities.reads_opportunities})
    return report
