"""Buying a field from a data provider, as a contract rather than as code.

The same shape as `CrmSource`, for the same reason: `docs/15` ranks a provider
shock — a price change, a policy change, an API withdrawn — as a high-impact
risk, and the mitigation it names is per-field abstraction with at least two
providers behind every critical field. That mitigation is only real if adding
the second provider is implementing an interface rather than editing the
runtime.

Three fields, because `docs/12` prices three:

| Field | Credits | What a hit means |
|---|---|---|
| `email` | 8 | A deliverable address, verified by whoever sold it |
| `phone` | 25 | A mobile number, the most expensive thing on the list |
| `firmographics` | 4 | Employee band, industry, country for an account |

A provider that sells two of them is one registration with two fields: it is
one contract and one credential, and splitting it would make the waterfall
believe it has more independent providers than it has.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol

FIELDS = ("email", "phone", "firmographics")


class ProviderError(RuntimeError):
    """The provider could not be asked. Distinct from a miss.

    A miss is an answer — it lowers the measured hit rate and the waterfall
    learns from it. An error is the absence of an answer, and counting it as a
    miss would teach the optimiser that a broken integration is a bad provider.
    """


@dataclass(frozen=True)
class Lookup:
    """What is known, and what is wanted.

    The whole entity goes in rather than a chosen subset, because which facts
    a provider can use is the provider's business: one resolves an email from
    a name and a company domain, another from a LinkedIn URN, and the caller
    should not have to know which.
    """
    field: str
    entity: dict[str, Any]
    account: dict[str, Any] | None = None


@dataclass(frozen=True)
class Found:
    """What came back, and what it cost.

    `cost_micros` is reported by the connector rather than read from the
    registration, because a provider that bills per credit-bundle or charges
    differently on a miss knows its own arithmetic and the registration only
    holds the estimate the waterfall plans with.
    """
    hit: bool
    values: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    cost_micros: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DataCapabilities:
    provider: str
    fields: tuple[str, ...]
    # Whether a lookup that returns nothing is still charged. It changes which
    # order is cheapest, so the optimiser has to be told.
    billed_on_miss: bool = False

    def __post_init__(self) -> None:
        unknown = set(self.fields) - set(FIELDS)
        if unknown:
            raise ValueError(
                f"{self.provider} claims fields nobody prices: {sorted(unknown)}; "
                f"known: {', '.join(FIELDS)}")


class DataSource(Protocol):
    """One provider, one credential, one or more fields."""
    capabilities: DataCapabilities

    def resolve(self, lookup: Lookup, credential: str | None,
                config: dict[str, Any]) -> Found: ...


_SOURCES: dict[str, DataSource] = {}


def register_provider(source: DataSource) -> DataSource:
    _SOURCES[source.capabilities.provider] = source
    return source


def get_provider(provider: str) -> DataSource:
    if provider not in _SOURCES:
        known = ", ".join(sorted(_SOURCES)) or "none"
        raise LookupError(f"no data provider '{provider}'; registered: {known}")
    return _SOURCES[provider]


def providers() -> list[str]:
    return sorted(_SOURCES)


def cohort_of(account: dict[str, Any] | None) -> str:
    """The segment a hit rate is scored in.

    `docs/07` keys the matrix on country, size, sector and seniority. This uses
    the first two and stops there, deliberately: a matrix keyed finely enough
    to be interesting is a matrix whose cells never accumulate enough calls to
    mean anything, and the optimiser reads a cell with four observations in it
    as confidently as one with four thousand.

    Unknown parts collapse to `xx`, so a cohort is always a real key and the
    fallback is a cell rather than a missing one.
    """
    account = account or {}
    country = (account.get("country") or "xx").strip().lower()[:2] or "xx"
    band = (account.get("employee_band") or "xx").strip().lower()
    return f"{country}_{band}"


def unresolved(entity: dict[str, Any], field_name: str) -> bool:
    """Whether this field is worth paying for.

    Asked before every lookup, because the cheapest provider is the one you do
    not call. A value already present is not re-bought, and neither is one a
    previous attempt proved does not exist.
    """
    if field_name == "email":
        return not entity.get("email")
    if field_name == "phone":
        return not entity.get("phone")
    if field_name == "firmographics":
        return not (entity.get("employee_band") and entity.get("industry_code"))
    raise ValueError(f"'{field_name}' is not a priced field: {', '.join(FIELDS)}")


def iter_fields() -> Iterator[str]:
    yield from FIELDS
