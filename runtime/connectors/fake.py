"""An in-memory connector.

Used by the tests and by a tenant in dry run. It records what it was asked to
do and enforces the same idempotency contract as a real provider, so a test
that passes here is testing the runtime's delivery semantics rather than a
provider's.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from runtime.connectors.dataprovider import (DataCapabilities, Found, Lookup,
                                            ProviderError)
from runtime.connectors.signalsource import (Detection, SignalSourceError,
                                            SourceCapabilities)
from runtime.connectors.base import Request, Result, TransientError


@dataclass
class FakeConnector:
    provider: str = "fake"
    channels: frozenset[str] = frozenset({"email", "linkedin", "task", "ads", "webhook", "crm"})
    sent: dict[str, Request] = field(default_factory=dict)
    fail_times: int = 0
    _failures: int = 0
    cost_micros: int = 1200

    def execute(self, request: Request) -> Result:
        if self._failures < self.fail_times:
            self._failures += 1
            raise TransientError(f"injected failure {self._failures}/{self.fail_times}")
        if request.idempotency_key in self.sent:
            # What a well-behaved provider does with a repeated key: acknowledge
            # the original, charge nothing, send nothing.
            return Result(ok=True, provider_ref=f"dup-{request.idempotency_key}",
                          cost_micros=0, detail={"deduplicated": True})
        self.sent[request.idempotency_key] = request
        return Result(ok=True, provider_ref=f"fake-{len(self.sent)}",
                      cost_micros=0 if request.dry_run else self.cost_micros,
                      detail={"dry_run": request.dry_run})

    def reset(self) -> None:
        self.sent.clear()
        self._failures = 0


@dataclass
class FakeDataProvider:
    """A data provider whose hit rate is decided by the test, not by chance.

    Deterministic on purpose. A provider that hits at random makes a waterfall
    test that passes most of the time, which is the kind that gets believed
    until the day it does not.
    """
    key: str = "fake-data"
    fields: tuple[str, ...] = ("email", "phone", "firmographics")
    hits: bool = True
    cost_micros: int = 30_000
    confidence: float = 0.92
    calls: list[Lookup] = field(default_factory=list)
    seen_credentials: list[str | None] = field(default_factory=list)
    error_times: int = 0
    _errors: int = 0
    values: dict[str, Any] = field(default_factory=dict)

    @property
    def capabilities(self) -> DataCapabilities:
        return DataCapabilities(provider=self.key, fields=self.fields)

    def resolve(self, lookup: Lookup, credential: str | None,
                config: dict[str, Any]) -> Found:
        if self._errors < self.error_times:
            self._errors += 1
            raise ProviderError(f"injected provider error {self._errors}")
        self.calls.append(lookup)
        self.seen_credentials.append(credential)
        if not self.hits:
            return Found(hit=False, cost_micros=self.cost_micros)
        default = {
            "email": {"email": f"found-{len(self.calls)}@example.com"},
            "phone": {"phone": f"+3460000{len(self.calls):04d}"},
            "firmographics": {"employee_band": "51-200", "industry_code": "62.01",
                              "country": "ES"},
        }[lookup.field]
        return Found(hit=True, values=self.values or default,
                     confidence=self.confidence, cost_micros=self.cost_micros)


@dataclass
class FakeSignalSource:
    """A signal source whose detections the test chooses.

    Deterministic, like the fake data provider and for the same reason: a
    source that fires at random makes a watcher test that passes most of the
    time, which is the kind that gets believed.
    """
    connector: str = "fake-feed"
    entities: tuple[str, ...] = ("account",)
    # None means every subject; an empty list means none of them. A single
    # falsy sentinel for both would make "detect nothing" silently mean
    # "detect everything", which is the wrong way round for a test to fail.
    detects: "list[str] | None" = None
    observed_at: "datetime | None" = None
    confidence: float = 1.0
    payload: dict[str, Any] = field(default_factory=dict)
    asked: list[tuple[str, int]] = field(default_factory=list)
    error_times: int = 0
    _errors: int = 0

    @property
    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(connector=self.connector, entities=self.entities)

    def detect(self, signal_key, subjects, credential, config) -> list[Detection]:
        from datetime import datetime, timezone

        if self._errors < self.error_times:
            self._errors += 1
            raise SignalSourceError(f"injected source error {self._errors}")
        subjects = list(subjects)
        self.asked.append((signal_key, len(subjects)))
        wanted = ({s.entity_id for s in subjects} if self.detects is None
                  else set(self.detects))
        moment = self.observed_at or datetime.now(timezone.utc)
        return [Detection(entity_id=s.entity_id, observed_at=moment,
                          payload=self.payload or {"detected_by": self.connector},
                          confidence=self.confidence)
                for s in subjects if s.entity_id in wanted]
