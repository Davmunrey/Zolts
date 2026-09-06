"""Turning provider events into outcomes, suppressions and touch status.

This is the half of the loop that makes incrementality measurable at all.
Without it outcomes arrive only by hand, and a product whose central claim is
measured lift depends on someone remembering to post them.

Three rules govern everything here:

1. The raw body is stored before it is interpreted. A provider that changes its
   payload, or an interpretation this runtime gets wrong, is then a replay
   rather than data that never existed.
2. Every effect is idempotent on the provider's own event id. Providers retry,
   and a retried unsubscribe that recorded a second outcome would corrupt the
   measurement it exists to feed.
3. An opt-out suppresses, exits the enrollment and cancels its queued work in
   one transaction. Doing two of the three is the failure this repository has
   already found four times in other disguises: the contact asked not to be
   contacted, and tomorrow's step still goes out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # the effects layer must import without the agent layer
    from runtime.agents.triage import Triage

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from runtime.db import one
from runtime.repo import actions, enrollments, entities, ledger

# What a provider event means to the runtime. Anything unmapped is stored and
# left unhandled rather than guessed at: a wrong mapping writes an outcome that
# silently moves a measured lift.
OPT_OUT = {"unsubscribe", "unsubscribed", "email_unsubscribed", "spam", "complaint",
           "contact.unsubscribed"}
NEGATIVE_DELIVERY = {"bounce", "bounced", "hard_bounce", "email_bounced", "dropped"}
ENGAGEMENT = {"open": "opened", "opened": "opened", "click": "opened",
              "email_opened": "opened", "delivered": "delivered",
              "email_delivered": "delivered"}
REPLY = {"reply", "replied", "email_reply", "lead_replied"}
CONVERSION = {"deal.creation": "opp_created", "deal_created": "opp_created",
              "meeting.booked": "meeting", "meeting_booked": "meeting",
              "opportunity_created": "opp_created", "deal.won": "won"}


@dataclass
class Applied:
    event_id: str
    effects: list[str] = field(default_factory=list)
    matched: bool = False


# Where each provider puts the words a person actually wrote. Several never
# send them, which is why an absent body leaves the existing behaviour alone
# rather than being read as a verdict of its own.
_TEXT_PATHS = (
    ("reply_message", "text"), ("reply_message", "html"), ("reply", "text"),
    ("message", "text"), ("email", "body"), ("body_text",), ("reply_body",),
    ("text",), ("body",),
)


def _reply_text(payload: dict[str, Any]) -> str | None:
    for path in _TEXT_PATHS:
        current: Any = payload
        for key in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        if isinstance(current, str) and current.strip():
            return current
    return None


def _normalise(provider: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Reduce a provider payload to the fields the runtime acts on.

    Providers disagree about names for the same three facts: what happened, to
    whom, and under which of our idempotency keys. Normalising here keeps that
    disagreement in one file instead of spread through the handlers.
    """
    if provider == "smartlead":
        return {
            "text": _reply_text(payload),
            "type": str(payload.get("event_type") or payload.get("event") or "").lower(),
            "email": payload.get("to_email") or payload.get("lead_email")
                     or (payload.get("lead") or {}).get("email"),
            "external_id": payload.get("id") or payload.get("event_id"),
            "idempotency_key": ((payload.get("lead") or {}).get("custom_fields") or {})
                               .get("zolts_idempotency_key"),
            "occurred_at": payload.get("event_timestamp") or payload.get("time"),
        }
    if provider == "hubspot":
        properties = payload.get("properties") or {}
        return {
            "type": str(payload.get("subscriptionType") or payload.get("eventType")
                        or payload.get("type") or "").lower(),
            "email": payload.get("email") or properties.get("email"),
            "external_id": str(payload.get("eventId") or payload.get("objectId") or "") or None,
            "idempotency_key": properties.get("zolts_idempotency_key"),
            "occurred_at": payload.get("occurredAt"),
            "value_micros": _euros_to_micros(properties.get("amount")),
        }
    return {
        "text": _reply_text(payload),
        "type": str(payload.get("type") or "").lower(),
        "email": payload.get("email"),
        "external_id": payload.get("id"),
        "idempotency_key": payload.get("idempotency_key"),
        "occurred_at": payload.get("occurred_at"),
    }


def _euros_to_micros(amount: Any) -> int | None:
    try:
        return int(round(float(amount) * 1_000_000))
    except (TypeError, ValueError):
        return None


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    for parse in (lambda v: datetime.fromisoformat(str(v).replace("Z", "+00:00")),
                  lambda v: datetime.fromtimestamp(float(v) / 1000, tz=timezone.utc),
                  lambda v: datetime.fromtimestamp(float(v), tz=timezone.utc)):
        try:
            parsed = parse(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError, OSError):
            continue
    return datetime.now(timezone.utc)


def store(cur, tenant_id: str, *, provider: str, payload: dict[str, Any],
          signature_ok: bool) -> dict[str, Any] | None:
    """Persist the raw event. Returns None when it is a retry already stored."""
    fields = _normalise(provider, payload)
    cur.execute(
        "insert into inbound_event (tenant_id, provider, event_type, external_id,"
        " payload, signature_ok) values (%s,%s,%s,%s,%s,%s)"
        " on conflict (tenant_id, provider, external_id) where external_id is not null"
        " do nothing returning *",
        (tenant_id, provider, fields["type"], fields["external_id"],
         json.dumps(payload), signature_ok))
    return one(cur)


def apply(cur, tenant_id: str, event: dict[str, Any],
          triage: "Triage | None" = None) -> Applied:
    """Interpret one stored event. Idempotent, and safe to re-run on a replay.

    `triage` is a reading of the reply's text, produced by the agent layer and
    passed in rather than fetched here: this module performs the effects and
    does not decide what a message means, and it must keep working with the
    agent layer switched off.

    Without a usable verdict a reply is recorded exactly as it was before —
    `reply_positive`. That over-counts, and it is a decision rather than an
    oversight: changing it silently would move every tenant's measured lift on
    a deploy, and a provider that reports a reply with no body has told us only
    that a human responded.
    """
    provider = event["provider"]
    fields = _normalise(provider, event["payload"])
    result = Applied(event_id=str(event["id"]))
    occurred = _parse_time(fields.get("occurred_at"))

    touch = None
    if fields.get("idempotency_key"):
        cur.execute("select * from touch where idempotency_key = %s",
                    (fields["idempotency_key"],))
        touch = one(cur)
    person = None
    if fields.get("email"):
        cur.execute("select * from person where email = %s", (fields["email"],))
        person = one(cur)
    result.matched = bool(touch or person)

    event_type = fields["type"]
    enrollment_id = str(touch["enrollment_id"]) if touch and touch["enrollment_id"] else None

    if event_type in OPT_OUT:
        _opt_out(cur, tenant_id, person, enrollment_id, provider, result)
    elif event_type in NEGATIVE_DELIVERY:
        _bounce(cur, tenant_id, touch, person, provider, result)
    elif event_type in ENGAGEMENT and touch:
        cur.execute("update touch set status = %s where id = %s",
                    (ENGAGEMENT[event_type], touch["id"]))
        result.effects.append(f"touch.{ENGAGEMENT[event_type]}")
    elif event_type in REPLY:
        if touch:
            cur.execute("update touch set status = 'replied' where id = %s", (touch["id"],))
            result.effects.append("touch.replied")
        _reply(cur, tenant_id, person, enrollment_id, occurred, provider, event,
               triage, result)
    elif event_type in CONVERSION:
        _outcome(cur, tenant_id, enrollment_id, CONVERSION[event_type],
                 fields.get("value_micros"), occurred, provider, event, result)

    cur.execute("update inbound_event set handled = true, handled_at = now() where id = %s",
                (event["id"],))
    return result


def _reply(cur, tenant_id: str, person: dict[str, Any] | None,
           enrollment_id: str | None, occurred: Any, provider: str,
           event: dict[str, Any], triage: "Triage | None", result: Applied) -> None:
    """Record what the reply was, rather than assuming it was a win."""
    if triage is None or not triage.usable:
        # Unchanged, and now counted. Nobody read this reply; the measurement
        # says how much of the reported lift rests on ones like it rather than
        # deflating every tenant's number on a deploy.
        _outcome(cur, tenant_id, enrollment_id, "reply_positive", None, occurred,
                 provider, event, result)
        return

    result.effects.append(f"triage.{triage.verdict}")
    if triage.verdict == "unsubscribe":
        # The same path a provider's unsubscribe event takes. An opt-out
        # written in prose is an opt-out, and routing it anywhere else would
        # mean two suppression mechanisms that have to agree forever.
        _opt_out(cur, tenant_id, person, enrollment_id, provider, result)
        return

    _outcome(cur, tenant_id, enrollment_id, f"reply_{triage.verdict}", None, occurred,
             provider, event, result, verified_by="triage")


def _opt_out(cur, tenant_id: str, person: dict[str, Any] | None, enrollment_id: str | None,
             provider: str, result: Applied) -> None:
    """Suppress, exit and cancel. All three, or the contact is contacted again."""
    if person and person.get("email"):
        entities.suppress(cur, tenant_id, "email", str(person["email"]),
                          "unsubscribed", provider)
        # Consent state and the suppression list are both consulted, by
        # different rules. Recording only one leaves a path that still allows.
        # The parameter is cast: Postgres cannot infer a type for an untyped
        # placeholder inside jsonb_build_object and raises rather than guessing.
        cur.execute(
            "update person set consent_state = consent_state ||"
            " jsonb_build_object('email', jsonb_build_object('opted_out', true,"
            " 'source', %s::text)) where id = %s", (provider, person["id"]))
        result.effects.extend(["suppression.email", "consent.opted_out"])

    if enrollment_id:
        enrollments.exit_enrollment(cur, enrollment_id, "opted_out")
        cur.execute(
            "update action set state = 'cancelled', last_error = 'contact opted out',"
            " updated_at = now() where enrollment_id = %s and state in ('pending','leased')",
            (enrollment_id,))
        result.effects.extend(["enrollment.exited", "actions.cancelled"])
    elif person:
        # No enrollment on the touch, so cancel by subject instead. An opt-out
        # that only stops one sequence is not an opt-out.
        cur.execute(
            "update action a set state = 'cancelled', last_error = 'contact opted out',"
            " updated_at = now() from enrollment e"
            " where a.enrollment_id = e.id and e.entity_id = %s"
            " and a.state in ('pending','leased')", (person["id"],))
        if cur.rowcount:
            result.effects.append("actions.cancelled")

    ledger.audit(cur, tenant_id, actor=f"webhook:{provider}", action="contact.opted_out",
                 subject=str(person["id"]) if person else None,
                 detail={"enrollment_id": enrollment_id})


def _bounce(cur, tenant_id: str, touch: dict[str, Any] | None,
            person: dict[str, Any] | None, provider: str, result: Applied) -> None:
    if touch:
        cur.execute("update touch set status = 'bounced' where id = %s", (touch["id"],))
        result.effects.append("touch.bounced")
    if person and person.get("email"):
        cur.execute("update person set email_status = 'invalid' where id = %s",
                    (person["id"],))
        # A hard bounce is an address that does not exist. Continuing to send to
        # it is what moves a domain's reputation, so it is suppressed, not just
        # marked.
        entities.suppress(cur, tenant_id, "email", str(person["email"]),
                          "hard_bounce", provider)
        result.effects.extend(["person.invalid", "suppression.email"])


def _outcome(cur, tenant_id: str, enrollment_id: str | None, outcome_type: str,
             value_micros: int | None, occurred_at: datetime, provider: str,
             event: dict[str, Any], result: Applied,
             verified_by: str | None = None) -> None:
    # Keyed on the stored event, so a provider retry that reached a second
    # worker cannot record the conversion twice and move the measured lift.
    recorded = ledger.record_outcome(
        cur, tenant_id, enrollment_id=enrollment_id, account_id=None, type=outcome_type,
        value_micros=value_micros, occurred_at=occurred_at, source=provider,
        dedupe_key=f"{provider}:{event['id']}", verified_by=verified_by)
    if recorded:
        result.effects.append(f"outcome.{outcome_type}")
