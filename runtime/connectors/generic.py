"""A CRM source defined by a document rather than by code.

A partner may run a system that is not on the market — their own, with field
names nobody here can guess. No connector written in this repository can read
it, so the connector stops being the unit of work and the mapping becomes it.

`GenericSource` implements the same `CrmSource` protocol as HubSpot and
Pipedrive, and passes the same contract suite. That is the point: a mapping is
not a lesser integration with a separate code path, it is a first-class source
whose behaviour happens to be configuration.

Two transports, because a CRM is either reachable or it is not:

* `http` — the runtime pulls, paging the way the mapping says it pages.
* `push` — the tenant posts batches, because their system is behind a firewall
  or has no API at all. Same mapping, same normaliser, same contract.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from runtime.connectors.crm import (Capabilities, Consent, CrmAccount, CrmContact,
                                    CrmOpportunity, DealStatus)
from runtime.connectors.http import body, request
from zolts.mapping import MappingError, TranslationRule, build, records_at

MAX_PAGES = 10_000  # a mapping that never terminates is a bug, not a big portal


class GenericSource:
    """One tenant's mapping, behaving like any other CRM source."""

    def __init__(self, mapping: dict[str, Any], credential: str | None = None) -> None:
        self.mapping = mapping
        self.spec = mapping["spec"]
        self.transport = self.spec["transport"]
        self._credential = credential
        consent = (self.spec["contacts"].get("consent") or None)
        self._consent = (TranslationRule(field=consent["field"],
                                         values=consent.get("values") or {},
                                         default=consent.get("default", "unknown"))
                         if consent else None)
        status = ((self.spec.get("opportunities") or {}).get("status") or None)
        self._deal_status = (TranslationRule(field=status["field"],
                                             values=status.get("values") or {},
                                             default=status.get("default", "open"))
                             if status else None)
        # A mapping reads deals when it says where they are and how to read
        # them. Both halves are needed: an endpoint with no field map returns
        # records nothing can build, and a field map with no endpoint has
        # nothing to build from.
        self._reads_deals = bool(self.spec.get("opportunities")) and (
            self.transport["kind"] == "push" or bool(self.transport.get("opportunities")))
        provider = mapping["metadata"]["provider"]
        self.capabilities = Capabilities(
            provider=provider,
            reads_accounts=True,
            reads_contacts=True,
            # Declared by the presence of a consent block, not by a flag
            # somebody sets. A mapping cannot claim to read opt-out state
            # without saying which field carries it.
            reads_opt_out=self._consent is not None,
            reads_opportunities=self._reads_deals,
            writes_tasks=False,
            page_size=int(((self.transport.get("contacts") or {}).get("pagination") or {})
                          .get("size", 100)),
            caveats=self._caveats(provider))

    def _caveats(self, provider: str) -> tuple[str, ...]:
        caveats = [f"'{provider}' is a tenant-authored mapping, not a connector "
                   "maintained here; its correctness is the tenant's to verify"]
        if self._consent is None:
            caveats.append("the mapping declares no consent field, so every contact "
                           "is stored with consent unknown")
        if not self._reads_deals:
            caveats.append("the mapping declares no opportunities, so this source "
                           "cannot say which accounts are already in a deal; a "
                           "program whose audience excludes them will refuse to "
                           "enrol rather than contact them")
        if self.transport["kind"] == "push":
            caveats.append("records arrive by push; this runtime cannot pull them")
        return tuple(caveats)

    # -- the contract ----------------------------------------------------

    def accounts(self, credential: str) -> Iterator[CrmAccount]:
        for record in self._records("accounts", credential):
            built = build(record, self.spec["accounts"])
            if not built.get("external_id") or not built.get("name"):
                continue
            yield CrmAccount(
                external_id=str(built["external_id"]), name=str(built["name"]),
                domain=_text(built.get("domain")), country=_text(built.get("country")),
                employee_band=_text(built.get("employee_band")),
                industry=_text(built.get("industry")))

    def contacts(self, credential: str) -> Iterator[CrmContact]:
        fields = {k: v for k, v in self.spec["contacts"].items() if k != "consent"}
        for record in self._records("contacts", credential):
            built = build(record, fields)
            email = _text(built.get("email"))
            if not built.get("external_id") or not email:
                continue
            yield CrmContact(
                external_id=str(built["external_id"]), email=email,
                full_name=_text(built.get("full_name")), title=_text(built.get("title")),
                country=_text(built.get("country")),
                account_external_id=_text(built.get("account_external_id")),
                consent=self._read_consent(record))

    def opportunities(self, credential: str) -> Iterator[CrmOpportunity]:
        if not self._reads_deals:
            return
        fields = {k: v for k, v in self.spec["opportunities"].items() if k != "status"}
        for record in self._records("opportunities", credential):
            built = build(record, fields)
            if not built.get("external_id"):
                continue
            yield CrmOpportunity(
                external_id=str(built["external_id"]),
                account_external_id=_text(built.get("account_external_id")),
                name=_text(built.get("name")),
                stage=_text(built.get("stage")),
                status=self._read_status(record),
                currency=_text(built.get("currency")),
                owner=_text(built.get("owner")))

    def _read_status(self, record: Any) -> DealStatus:
        """A state nobody mapped is open.

        The opposite of the consent default, and for the same reason: open
        means "leave this account alone", so an unrecognised state costs a
        sequence nobody sent rather than a sequence into a live deal.
        """
        if self._deal_status is None:
            return DealStatus.OPEN
        try:
            return DealStatus(self._deal_status.read(record))
        except ValueError:  # pragma: no cover - the schema constrains the values
            return DealStatus.OPEN

    def _read_consent(self, record: Any) -> Consent:
        if self._consent is None:
            return Consent.UNKNOWN
        try:
            return Consent(self._consent.read(record))
        except ValueError:  # pragma: no cover - the schema constrains the values
            return Consent.UNKNOWN

    # -- transport -------------------------------------------------------

    def _records(self, resource: str, credential: str) -> Iterator[Any]:
        # Staged records win over the transport. Holding a posted batch and
        # calling the tenant's API anyway would ignore the payload and reach
        # out over the network to answer a question already answered.
        if self._staged is not None or self.transport["kind"] == "push":
            yield from self._pushed(resource)
            return
        yield from self._pull(resource, credential)

    def _pushed(self, resource: str) -> Iterator[Any]:
        """Records handed to this source rather than fetched by it."""
        yield from (self._staged or {}).get(resource, [])

    _staged: dict[str, list[Any]] | None = None

    def stage(self, resource: str, records: list[Any]) -> None:
        """Hand a posted batch to the mapping. Used by the push endpoint."""
        self._staged = dict(self._staged or {})
        self._staged[resource] = records

    def _pull(self, resource: str, credential: str) -> Iterator[Any]:
        endpoint = self.transport[resource]
        auth = self.transport.get("auth") or {"kind": "bearer"}
        pagination = endpoint.get("pagination") or {"kind": "none"}
        url = self.transport["base_url"].rstrip("/") + "/" + endpoint["path"].lstrip("/")

        params: dict[str, Any] = dict(endpoint.get("params") or {})
        headers: dict[str, str] = {}
        token = None
        if auth["kind"] == "bearer":
            token = credential
        elif auth["kind"] == "header":
            headers[auth.get("name") or "x-api-key"] = credential
        elif auth["kind"] == "query":
            params[auth.get("name") or "api_key"] = credential

        size = int(pagination.get("size", 100))
        if pagination.get("size_param"):
            params[pagination["size_param"]] = size

        offset, cursor, pages = 0, None, 0
        while pages < MAX_PAGES:
            pages += 1
            page_params = dict(params)
            if pagination["kind"] == "offset":
                page_params[pagination["param"]] = offset
            elif pagination["kind"] == "cursor" and cursor is not None:
                page_params[pagination["param"]] = cursor

            payload = body(request("GET", url, token=token, params=page_params,
                                   headers=headers or None))
            page = records_at(payload, endpoint.get("records"))
            yield from page

            if pagination["kind"] == "none" or not page:
                return
            if pagination["kind"] == "offset":
                more_path = pagination.get("more_path")
                if more_path:
                    from zolts.mapping import MISSING, extract

                    more = extract(payload, more_path)
                    if more is MISSING or not more:
                        return
                elif len(page) < size:
                    # No explicit "there is more" flag, so a short page is the
                    # end. Paging past it forever is the failure this replaces.
                    return
                offset += len(page)
            else:
                from zolts.mapping import MISSING, extract

                cursor = extract(payload, pagination["cursor_path"])
                if cursor is MISSING or not cursor:
                    return
        raise MappingError(f"'{self.capabilities.provider}' paged {MAX_PAGES} times "
                           f"without terminating; check the {resource} pagination")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
