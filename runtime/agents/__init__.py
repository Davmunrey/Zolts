"""The agent layer.

Product invariant 1, and ADR-004: agents propose, the runtime disposes. Nothing
in this package performs an external action. An agent emits a proposal; the
proposal passes the eval gate, the policy gate and the spend guard before the
runtime turns it into an action, and the action is what the worker dispatches.
"""

from runtime.agents.client import ModelClient
from runtime.agents.copywriter import Draft, draft
from runtime.agents.spend import SpendGuard, SpendVerdict

__all__ = ["Draft", "ModelClient", "SpendGuard", "SpendVerdict", "draft"]
