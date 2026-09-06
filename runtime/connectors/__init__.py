"""Connectors: the only code in the runtime permitted to make outbound calls.

A connector receives an action that has already been planned, gated and
persisted. Its contract is narrow on purpose:

* It is handed an idempotency key and must pass it to the provider, or emulate
  it, so that a redelivered action does not produce a second send.
* It returns a `Result`, it does not decide policy. A connector that refuses to
  act returns `retryable=False` and the runtime cancels rather than retries.
* It reports cost in micros so the budget rules see real spend, not estimates.
"""

from runtime.connectors.base import Connector, Result, TransientError
from runtime.connectors.registry import get_connector, register

__all__ = ["Connector", "Result", "TransientError", "get_connector", "register"]
