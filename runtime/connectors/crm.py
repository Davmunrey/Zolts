"""The CRM contract.

ADR-006: every connector implements the same interface and passes a contract
suite in CI, because connector debt is the sector's main engineering sink. This
is the read half of that interface — the half that was missing while `sync.py`
had eleven HubSpot field names compiled into it.

Adding a CRM is now: map its objects onto `CrmAccount`, `CrmContact` and
`CrmOpportunity`, declare what it can and cannot tell you, and pass the
contract suite. No new sync, no new transaction handling, no new consent
logic.

One field here does more work than the rest.

`Capabilities.reads_opt_out` is the difference between a connector and a
liability. A CRM that does not expose whether a contact unsubscribed must not
have its silence read as permission: the sync records consent as *unknown*, the
policy gate denies on unknown, and a human is told which CRM cannot answer the
question. Defaulting to a legal basis because the field was absent is how a
platform sends to people who opted out somewhere it could not see.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class Consent(str, Enum):
    """What the CRM says about contacting this person.

    `UNKNOWN` is not a synonym for `ALLOWED`. It is the answer when the source
    cannot tell, and the policy gate treats it as a denial.
    """
    ALLOWED = "allowed"
    OPTED_OUT = "opted_out"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CrmAccount:
    """A company, in this runtime's vocabulary rather than the provider's."""
    external_id: str
    name: str
    domain: str | None = None
    country: str | None = None
    employee_band: str | None = None
    industry: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CrmContact:
    external_id: str
    email: str | None = None
    full_name: str | None = None
    title: str | None = None
    country: str | None = None
    account_external_id: str | None = None
    consent: Consent = Consent.UNKNOWN
    attributes: dict[str, Any] = field(default_factory=dict)


class DealStatus(str, Enum):
    """Where a deal stands, in three states every CRM can be mapped onto.

    The provider's own stage label is kept beside this, never instead of it: a
    pipeline called "Negotiation / Legal" means something to the operator and
    nothing to a query, and a query is what an audience runs.
    """
    OPEN = "open"
    WON = "won"
    LOST = "lost"


@dataclass(frozen=True)
class CrmOpportunity:
    """A deal. The object an audience needs in order to leave people alone.

    `status` is the connector's mapping, not ours. Reading "Closed Won" out of
    a stage name we do not own is how a won deal becomes an open one, and the
    cost of that mistake is an outbound sequence into an account the sales team
    has already closed.
    """
    external_id: str
    account_external_id: str | None = None
    name: str | None = None
    stage: str | None = None
    status: DealStatus = DealStatus.OPEN
    amount_micros: int | None = None
    currency: str | None = None
    owner: str | None = None
    opened_at: Any = None
    closed_at: Any = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Capabilities:
    """What a CRM can and cannot answer.

    Declared rather than discovered, and asserted by the contract suite. A
    connector that claims `reads_opt_out` and returns `UNKNOWN` for every
    contact fails the suite, because a false claim here is worse than an
    honest gap: the gap is handled, the claim is trusted.
    """
    provider: str
    reads_accounts: bool = True
    reads_contacts: bool = True
    reads_opt_out: bool = False
    # Whether this source can say which accounts have a live deal. Declared,
    # never inferred: a source that returns nothing must be distinguishable
    # from one that was never asked, because an audience excluding accounts in
    # a deal reads both as "no deal" and contacts everybody.
    reads_opportunities: bool = False
    writes_tasks: bool = False
    page_size: int = 100
    # What this connector cannot do, in a sentence an operator can act on.
    caveats: tuple[str, ...] = ()


@runtime_checkable
class CrmSource(Protocol):
    """Reading a CRM into the canonical entities.

    Both methods return iterators and are expected to page internally. The sync
    commits in batches, so a generator that walks a hundred thousand records is
    the intended shape rather than a problem to solve at the call site.
    """
    capabilities: Capabilities

    def accounts(self, credential: str) -> Iterator[CrmAccount]: ...

    def contacts(self, credential: str) -> Iterator[CrmContact]: ...

    def opportunities(self, credential: str) -> Iterator[CrmOpportunity]:
        """Deals, or nothing when this source cannot read them.

        Part of the protocol rather than an optional extra, so that adding a
        CRM means answering the question. A source that answers "I cannot"
        declares `reads_opportunities=False` and yields nothing; the runtime
        then refuses to run an audience that depends on the answer instead of
        assuming it.
        """
        ...


_SOURCES: dict[str, CrmSource] = {}


def register_source(source: CrmSource) -> CrmSource:
    _SOURCES[source.capabilities.provider] = source
    return source


def get_source(provider: str) -> CrmSource:
    if provider not in _SOURCES:
        known = ", ".join(sorted(_SOURCES)) or "none"
        raise LookupError(f"no CRM source for '{provider}'; registered: {known}")
    return _SOURCES[provider]


def sources() -> list[str]:
    return sorted(_SOURCES)


def clear_sources() -> None:
    _SOURCES.clear()


def consent_state(contact: CrmContact, provider: str,
                  basis: str = "legitimate_interest") -> dict[str, Any]:
    """The consent record to store for this contact.

    A CRM that cannot answer produces `unknown`, never a basis. The policy
    engine has no rule that admits `unknown`, so the contact is unreachable
    until somebody establishes a basis — which is the correct outcome for a
    person whose wishes are not known.
    """
    if contact.consent is Consent.OPTED_OUT:
        return {"email": {"opted_out": True, "source": provider}}
    if contact.consent is Consent.ALLOWED:
        return {"email": {"basis": basis, "source": provider}}
    return {"email": {"basis": "unknown", "source": provider,
                      "note": f"{provider} does not expose opt-out state"}}
