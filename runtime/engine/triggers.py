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


def _event_matches(event: dict[str, Any], signal: dict[str, Any]) -> bool:
    if event.get("signal") != signal["type"]:
        return False
    where = event.get("where")
    if not where:
        return True
    # A `where` clause that references an absent field evaluates to False rather
    # than raising: signal payloads are external data and a partial one is a
    # non-match, not an outage.
    return expr.evaluate(where, {"payload": signal.get("payload") or {}})


def matches(spec: dict[str, Any], signal: dict[str, Any],
            history: list[dict[str, Any]]) -> str | None:
    """Return the matching reason, or None.

    `history` is the entity's signals inside the trigger window, most recent
    first, and must already include the signal under test.
    """
    trigger = spec.get("trigger") or {}
    events = trigger.get("events") or []
    if not events:
        return None
    combine = trigger.get("combine", "any_within")

    fired = [e for e in events if any(_event_matches(e, s) for s in history)]
    if not any(_event_matches(e, signal) for e in events):
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
