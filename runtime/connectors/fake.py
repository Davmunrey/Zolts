"""An in-memory connector.

Used by the tests and by a tenant in dry run. It records what it was asked to
do and enforces the same idempotency contract as a real provider, so a test
that passes here is testing the runtime's delivery semantics rather than a
provider's.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
