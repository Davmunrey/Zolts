"""Going and looking for a signal, as a contract rather than as code.

The third contract in this runtime with the same shape, and for the same
reason each time: `CrmSource` so a partner's own CRM is an implementation,
`DataSource` so the second provider behind a critical field is one, and this
so a jobs feed, a press feed and a product event stream are three
implementations rather than three edits.

`docs/06` calls signal-to-action latency the highest-leverage variable in the
whole GTM system and treats it as a product SLA. A source that has to be
written into the runtime before it can be watched is a source that starts
watching a release later than it could have.

The shape is deliberately a batch: a feed is asked about many accounts at once
because that is how feeds are priced and rate-limited, and asking one at a
time is the difference between a six-hour refresh being affordable and not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Protocol


class SignalSourceError(RuntimeError):
    """The source could not be asked.

    Distinct from finding nothing, exactly as it is for a data provider: an
    empty answer is an answer, and a source that is down must not look like a
    quiet week.
    """


@dataclass(frozen=True)
class Subject:
    """One account or person to look at, with what is already known about it."""
    entity_type: str
    entity_id: str
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Detection:
    """Something happened, when it happened, and how sure the source is.

    `observed_at` is when the event occurred at the source, not when we noticed.
    The difference between those two is the detection latency, and reporting
    the time we noticed would hide exactly the number `docs/06` says matters
    most.
    """
    entity_id: str
    observed_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    dedupe_key: str | None = None


@dataclass(frozen=True)
class SourceCapabilities:
    connector: str
    entities: tuple[str, ...] = ("account",)
    # How many subjects this source will take in one call. A feed that answers
    # about a thousand accounts at once and one that answers about ten are
    # different products, and the watcher has to size its batches accordingly.
    batch_size: int = 200


class SignalSource(Protocol):
    """One source, asked about many subjects at once."""
    capabilities: SourceCapabilities

    def detect(self, signal_key: str, subjects: Iterable[Subject],
               credential: str | None, config: dict[str, Any]) -> list[Detection]: ...


_SOURCES: dict[str, SignalSource] = {}


def register_source(source: SignalSource) -> SignalSource:
    _SOURCES[source.capabilities.connector] = source
    return source


def get_source(connector: str) -> SignalSource:
    if connector not in _SOURCES:
        known = ", ".join(sorted(_SOURCES)) or "none"
        raise LookupError(f"no signal source '{connector}'; registered: {known}")
    return _SOURCES[connector]


def sources() -> list[str]:
    return sorted(_SOURCES)
