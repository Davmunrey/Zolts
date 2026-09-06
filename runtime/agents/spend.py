"""Cost governance, delegated to Trazum.

Before the runtime spends money on a model call it asks whether it may. The
answer comes from Trazum's `spend_guard` over MCP rather than from a second
pricing table in this repository: two implementations of one price drift, and
the one that drifts is the one that authorises the spend.

Three properties of that tool are what make it usable here:

* It never spends to answer. No provider call, no model call — the figures
  come from what the caller passes and the catalogue the server already holds.
* Without a ceiling somebody set, the answer is `cannot-tell`, never a yes
  nobody measured. That matches this runtime's own rule that an unmeasured
  thing is not a pass.
* A refusal carries the cheaper ways to make the same call, each priced for
  this call and each saying what it assumes.

Fail-closed. An unreachable guard means no spend, not free rein: a cost
control that opens when it breaks is not a cost control.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("zolts.spend")

DEFAULT_COMMAND = os.environ.get("ZOLTS_SPEND_GUARD_CMD", "trazum-mcp")
STARTUP_TIMEOUT = 20.0
CALL_TIMEOUT = 20.0


@dataclass(frozen=True)
class Alternative:
    """A cheaper way to make the same call, priced for this call."""
    kind: str
    model: str | None
    saving_usd: float
    fits: bool
    assumes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SpendVerdict:
    verdict: str                      # yes | no | cannot-tell
    estimated_usd: float | None
    reason: str | None
    rests_on: str | None
    alternatives: list[Alternative] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        """Only an explicit yes permits the spend.

        `cannot-tell` is not a yes. It is the guard saying nobody set a
        ceiling, and proceeding on it would spend against a budget that does
        not exist.
        """
        return self.verdict == "yes"

    @property
    def cheapest_alternative(self) -> Alternative | None:
        usable = [a for a in self.alternatives if a.fits and a.model]
        return max(usable, key=lambda a: a.saving_usd) if usable else None

    @property
    def estimated_micros(self) -> int:
        return int(round((self.estimated_usd or 0.0) * 1_000_000))


class SpendGuardUnavailable(RuntimeError):
    """The guard could not be reached. Callers treat this as a refusal."""


class SpendGuard:
    """A long-lived Trazum MCP process, one per worker.

    One process rather than one per call: a Node start-up in the dispatch path
    would cost more latency than the decision saves.
    """

    def __init__(self, command: str | None = None, *, enabled: bool = True) -> None:
        self._command = command or DEFAULT_COMMAND
        self._enabled = enabled
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._next_id = 0

    # -- process ---------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._enabled and shutil.which(self._command.split()[0]) is not None

    def _ensure(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        if not self.available:
            raise SpendGuardUnavailable(f"'{self._command}' is not on PATH")
        self._process = subprocess.Popen(  # noqa: S603 - operator-configured command
            self._command.split(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self._next_id = 0
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "zolts-runtime", "version": "0.1.0"}},
            timeout=STARTUP_TIMEOUT)
        self._notify("notifications/initialized")
        return self._process

    def close(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                self._process.kill()
            self._process = None

    # -- transport -------------------------------------------------------

    def _write(self, payload: dict[str, Any]) -> None:
        assert self._process is not None and self._process.stdin is not None
        self._process.stdin.write(json.dumps(payload) + "\n")
        self._process.stdin.flush()

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _rpc(self, method: str, params: dict[str, Any], *,
             timeout: float = CALL_TIMEOUT) -> dict[str, Any]:
        assert self._process is not None and self._process.stdout is not None
        self._next_id += 1
        request_id = self._next_id
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        # Responses are correlated by id rather than by order: the server is
        # free to interleave notifications, and reading the next line blindly
        # would eventually pair a verdict with the wrong call.
        deadline = threading.Event()
        timer = threading.Timer(timeout, deadline.set)
        timer.start()
        try:
            while not deadline.is_set():
                line = self._process.stdout.readline()
                if not line:
                    raise SpendGuardUnavailable("the guard closed its output")
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("id") == request_id:
                    if "error" in message:
                        raise SpendGuardUnavailable(str(message["error"]))
                    return message.get("result") or {}
        finally:
            timer.cancel()
        raise SpendGuardUnavailable(f"no answer to {method} within {timeout}s")

    # -- the decision ----------------------------------------------------

    def check(self, *, model: str, input_tokens: int, output_tokens: int = 0,
              consumed_usd: float | None = None, limit_usd: float | None = None,
              label: str | None = None, batch_eligible: bool = False) -> SpendVerdict:
        """Ask whether this call may be made.

        `consumed_usd` must be measured spend, never an estimate — the runtime
        takes it from its own `cost_event` ledger. `limit_usd` is the program's
        declared budget. Omitting either produces `cannot-tell`, which blocks.
        """
        arguments: dict[str, Any] = {"model": model, "inputTokens": int(input_tokens),
                                     "outputTokens": int(output_tokens)}
        if consumed_usd is not None:
            arguments["consumedUsd"] = round(float(consumed_usd), 6)
        if limit_usd is not None:
            arguments["limitUsd"] = round(float(limit_usd), 6)
        if label:
            arguments["label"] = label
        if batch_eligible:
            arguments["batchEligible"] = True

        with self._lock:
            try:
                self._ensure()
                result = self._rpc("tools/call",
                                   {"name": "spend_guard", "arguments": arguments})
            except SpendGuardUnavailable as exc:
                log.warning("spend guard unavailable, refusing the call: %s", exc)
                return SpendVerdict("cannot-tell", None, f"guard unavailable: {exc}", None)
            except OSError as exc:  # pragma: no cover - process died mid-write
                self._process = None
                return SpendVerdict("cannot-tell", None, f"guard unavailable: {exc}", None)

        return _parse(result)


def _parse(result: dict[str, Any]) -> SpendVerdict:
    blocks = result.get("content") or []
    text = next((b.get("text", "") for b in blocks if b.get("type") == "text"), "")
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        # The server answers a malformed request in prose rather than JSON.
        # That is a caller bug, and it must not read as permission.
        return SpendVerdict("cannot-tell", None, text[:200] or "unparseable answer", None)

    cost = payload.get("cost") or {}
    call = cost.get("call") or {}
    alternatives = [
        Alternative(kind=a.get("kind", "route"),
                    model=(a.get("model") or {}).get("id"),
                    saving_usd=float(a.get("savingUsd") or 0.0),
                    fits=bool(a.get("fits")),
                    assumes=[str(x.get("kind", x)) for x in (a.get("assumes") or [])])
        for a in (payload.get("alternatives") or [])
    ]
    return SpendVerdict(
        verdict=str(payload.get("verdict") or "cannot-tell"),
        estimated_usd=call.get("estimatedUsd"),
        reason=cost.get("reason"),
        rests_on=cost.get("restsOn"),
        alternatives=alternatives,
        raw=payload)
