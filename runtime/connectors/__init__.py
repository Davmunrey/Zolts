"""Connectors: the only code in the runtime permitted to make outbound calls.

A connector receives an action that has already been planned, gated and
persisted. Its contract is narrow on purpose:

* It is handed an idempotency key and must pass it to the provider, or emulate
  it, so that a redelivered action does not produce a second send.
* It returns a `Result`, it does not decide policy. A connector that refuses to
  act returns `retryable=False` and the runtime cancels rather than retries.
* It reports cost in micros so the budget rules see real spend, not estimates.
"""

from runtime.connectors.base import Connector, PermanentError, Result, TransientError
from runtime.connectors.crm import (Capabilities, Consent, CrmAccount, CrmContact,
                                    CrmSource, get_source, register_source, sources)
from runtime.connectors.hubspot import HubSpotConnector
from runtime.connectors.pipedrive import PipedriveConnector
from runtime.connectors.registry import get_connector, providers_for, register
from runtime.connectors.smartlead import SmartleadConnector

__all__ = ["Capabilities", "Connector", "Consent", "CrmAccount", "CrmContact", "CrmSource",
           "PermanentError", "Result", "TransientError", "get_connector", "get_source",
           "install_default_connectors", "providers_for", "register", "register_source",
           "sources"]


def install_default_connectors() -> None:
    """Register the providers a deployment ships with.

    The fake connector is deliberately not installed here. A tenant falling
    back to it would report success while sending nothing, which is the failure
    mode that makes a sending platform untrustworthy; tests register it
    explicitly.
    """
    register(HubSpotConnector())
    register(SmartleadConnector())
    # Read sources are a separate registry from send channels: a CRM you can
    # read is not automatically a channel you may write to.
    register_source(HubSpotConnector())
    register_source(PipedriveConnector())
