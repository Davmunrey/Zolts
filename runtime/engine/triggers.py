"""Trigger matching: does this signal enroll this entity into this program?"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from zolts import expr

_DURATION = re.compile(r"^(\d+)([hdw])$")
_UNITS = {"h": "hours", "d": "days", "w": "weeks"}


def parse_duration(text: str) -> timedelta:
    match = _DURATION.match(str(text).strip())
    if not match:
        raise ValueError(f"'{text}' is not a duration like '30d'")
    return timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})


@dataclass(frozen=True)
class Match:
    program_id: str
    program_key: str
    reason: str


@dataclass(frozen=True)
class Unanswerable:
    """A predicate the payload could not answer, and why.

    Not an error and not a match. The caller reports it — to the sender in the
    ingest response, and to the operator in the audit log — because a source
    quietly sending the wrong type looks exactly like a source sending nothing
    that matches, and the two need opposite responses.
    """
    clause: str
    detail: str


def _event_matches(event: dict[str, Any], signal: dict[str, Any],
                   notes: "list[Unanswerable] | None" = None) -> bool:
    if event.get("signal") != signal["type"]:
        return False
    where = event.get("where")
    if not where:
        return True
    # A `where` clause that references an absent field evaluates to False rather
    # than raising: signal payloads are external data and a partial one is a
    # non-match, not an outage.
    #
    # The same is true of a field that is present and unusable — a number sent
    # as a string, a null where a figure goes — and it was not. Comparing them
    # raises `TypeError`, which came out of `POST /v1/signals` as a 500: the
    # outage this comment says cannot happen, from the one input the runtime
    # does not control. Quoting numbers is what a great many JSON producers do
    # and what every form-encoded webhook does. D-37.
    try:
        return expr.evaluate(where, {"payload": signal.get("payload") or {}})
    except TypeError as exc:
        if notes is not None:
            notes.append(Unanswerable(clause=str(where), detail=str(exc)))
        return False


def matches(spec: dict[str, Any], signal: dict[str, Any],
            history: list[dict[str, Any]],
            *, notes: "list[Unanswerable] | None" = None) -> str | None:
    """Return the matching reason, or None.

    `history` is the entity's signals inside the trigger window, most recent
    first, and must already include the signal under test.

    `notes` collects the predicates the payload could not answer. Pass a list
    to hear about them; the enrolment path does, and turns each into a warning
    on the response and a row in the audit log.
    """
    trigger = spec.get("trigger") or {}
    events = trigger.get("events") or []
    if not events:
        return None
    combine = trigger.get("combine", "any_within")

    fired = [e for e in events if any(_event_matches(e, s, notes) for s in history)]
    if not any(_event_matches(e, signal, notes) for e in events):
        # The incoming signal itself must participate. Otherwise a program with
        # a satisfied history would re-fire on every unrelated signal that
        # happened to arrive inside the window.
        return None

    if combine == "all_within":
        if len(fired) != len(events):
            return None
        return "all events observed within window"
    if len(fired) >= 1:
        return f"{fired[0].get('signal')} matched"
    return None


def window_start(spec: dict[str, Any], now: datetime) -> datetime:
    trigger = spec.get("trigger") or {}
    return now - parse_duration(trigger.get("window", "30d"))


def cooldown_days(spec: dict[str, Any]) -> int:
    dedupe = (spec.get("trigger") or {}).get("dedupe") or {}
    cooldown = dedupe.get("cooldown")
    return int(parse_duration(cooldown).days) if cooldown else 0


def signal_types(spec: dict[str, Any]) -> list[str]:
    return [e["signal"] for e in (spec.get("trigger") or {}).get("events", []) if e.get("signal")]
