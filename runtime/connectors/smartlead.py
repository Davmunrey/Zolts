"""Smartlead: the sending channel.

Sending is the action with the least reversible consequences in the product, so
this connector is the strictest one:

* It refuses to send without an explicit campaign binding. Guessing a campaign
  means guessing whose sending reputation to spend.
* It reconciles rather than creates: adding a lead that already exists in the
  campaign returns the existing lead, so a redelivered action does not enqueue
  a second email.
* It does **not** report the mailbox actually used, and an earlier version of
  this docstring claimed it did. Adding a lead to a campaign hands Smartlead a
  recipient; Smartlead decides which of its mailboxes sends it and when. So the
  mailbox `runtime.fleet` allocates for a send through this connector is an
  intent, recorded on the touch, and not an observation.

  What the capacity gate therefore buys on this path is a bound on the volume
  Zolts releases per day, computed from the fleet it has been told about. That
  is a real control and it is not the same control as choosing the mailbox.
  Saying so is the point: a per-mailbox guarantee the provider never agreed to
  is exactly the kind of claim that is discovered to be false by a burned
  domain. Registered as decision 19.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime.connectors.base import PermanentError, Request, Result
from runtime.connectors.http import body, request

BASE = "https://server.smartlead.ai/api/v1"

# What one send costs the tenant, in micros of EUR. A real figure from the
# tenant's plan belongs in the connection config; this is the fallback so that
# budget rules see a non-zero cost rather than treating sending as free.
DEFAULT_COST_MICROS = 900


@dataclass
class SmartleadConnector:
    provider: str = "smartlead"
    channels: frozenset[str] = frozenset({"email"})
    base: str = BASE

    def execute(self, request_: Request) -> Result:
        if request_.secret is None:
            raise PermanentError("smartlead connection has no api key")
        contact = request_.contact or {}
        email = contact.get("email")
        if not email:
            raise PermanentError("no email address on the resolved contact")

        step = request_.step or {}
        campaign_id = step.get("campaign_id") or request_.config.get("campaign_id")
        if not campaign_id:
            raise PermanentError(
                "no campaign bound for this step; refusing to guess whose "
                "sending reputation to spend")

        if request_.dry_run:
            return Result(ok=True, provider_ref=f"dry-run:{email}", cost_micros=0,
                          detail={"dry_run": True, "campaign_id": campaign_id})

        payload = {
            "lead_list": [{
                "email": str(email),
                "first_name": (contact.get("full_name") or "").split(" ")[0] or None,
                "last_name": " ".join((contact.get("full_name") or "").split(" ")[1:]) or None,
                "company_name": (contact.get("attributes") or {}).get("company_name"),
                "custom_fields": {
                    "zolts_idempotency_key": request_.idempotency_key,
                    "zolts_step": step.get("step"),
                },
            }],
            # Smartlead's own guard against re-adding a lead that is already in
            # the campaign. It is the provider-side half of the idempotency
            # contract; the key in custom_fields is the half we can audit.
            "settings": {"ignore_duplicate_leads_in_other_campaign": False},
        }
        response = request("POST", f"{self.base}/campaigns/{campaign_id}/leads",
                           params={"api_key": request_.secret}, json=payload)
        result = body(response)

        already = int(result.get("already_added_to_campaign") or 0)
        uploaded = int(result.get("upload_count") or 0)
        if already and not uploaded:
            return Result(ok=True, provider_ref=f"existing:{email}", cost_micros=0,
                          detail={"deduplicated": True, "campaign_id": campaign_id})
        if not uploaded:
            raise PermanentError(f"smartlead accepted no leads: {result}")

        cost = int(request_.config.get("cost_micros_per_send") or DEFAULT_COST_MICROS)
        return Result(ok=True, provider_ref=str(result.get("bulk_email_id") or email),
                      cost_micros=cost,
                      detail={"campaign_id": campaign_id, "uploaded": uploaded})
