"""A ceiling on the unauthenticated path.

`POST /v1/signup` is the only route in this runtime that takes a write without
a key. Its tokens are 32 random bytes and single-use, so guessing one is not
the threat; volume is. Without a ceiling, one client can hold every worker
thread and the API stops answering for the tenants that are paying.

**This limiter is per process.** It keeps its counters in memory, so two
machines allow twice the traffic and a restart forgets everything. That is
stated rather than hidden: at the scale this runtime is deployed — one machine,
a handful of design partners — a per-process ceiling is most of the value for
none of the operational cost of shared state, and a limiter that needs Redis to
exist is a limiter nobody turns on. When a second machine matters, this is
replaced by a counter in Postgres, not extended.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

# Generous on purpose. A person filling in a signup form makes one request; a
# person who mistyped makes five. Anything above this is not a person.
DEFAULT_LIMIT = 20
DEFAULT_WINDOW_SECONDS = 60


class Throttle:
    """A sliding window per caller, counted in memory."""

    def __init__(self, limit: int = DEFAULT_LIMIT,
                 window_seconds: int = DEFAULT_WINDOW_SECONDS) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, caller: str, *, now: float | None = None) -> bool:
        """Whether this caller may proceed, counting this call against them."""
        moment = time.monotonic() if now is None else now
        cutoff = moment - self.window
        with self._lock:
            hits = self._hits[caller]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(moment)
            # An idle caller should not be remembered forever; a dictionary that
            # only grows is a slower outage than the one being prevented.
            if not hits:
                del self._hits[caller]
            return True

    def retry_after(self, caller: str, *, now: float | None = None) -> int:
        """Seconds until this caller's oldest hit falls out of the window."""
        moment = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.get(caller)
            if not hits:
                return 0
            return max(1, int(hits[0] + self.window - moment) + 1)

    def forget(self, before_seconds: int = DEFAULT_WINDOW_SECONDS * 10) -> int:
        """Drop callers with nothing inside the window. Returns how many."""
        cutoff = time.monotonic() - before_seconds
        with self._lock:
            stale = [c for c, hits in self._hits.items() if not hits or hits[-1] <= cutoff]
            for caller in stale:
                del self._hits[caller]
            return len(stale)


def caller_of(request) -> str:
    """Who to count against.

    Behind Fly and most proxies the peer address is the proxy, so the forwarded
    header is used when present. It is client-controlled and therefore
    spoofable: this is a ceiling on accidental and casual load, not an access
    control, and nothing security-relevant is decided by it.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"
