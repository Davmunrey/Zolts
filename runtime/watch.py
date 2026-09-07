"""Looking for the signals live programs are waiting on.

`docs/06` opens by calling signal-to-action latency the highest-leverage
variable in the whole GTM system, and a product SLA rather than an
implementation detail. A runtime that only learns about a funding round when
its customer types it into an API cannot have a latency of its own; it inherits
whatever the customer's was, and the SLA is a claim about somebody else's work.

Four rules, and the last two are the ones that decide whether this is
affordable:

**Only what a live program is waiting on.** Watching a signal no program
consumes is spending a data budget to fill a table. The catalogue is the menu;
the live programs are the order.

**A detection past its freshness SLA is refused, not ingested.** `docs/06`
sets the SLA from the tier's action window, and acting on a three-day-old
pricing-page visit with a 48-hour half-life spends a touch on somebody whose
moment has gone. It is recorded as stale rather than dropped silently, because
a source that keeps finding things too late is a source to replace and that
only shows up if the staleness is counted.

**A check is billed once per account per day.** `docs/12` prices it that way
and the consequence is the point: a definition refreshing every six hours costs
the same as one refreshing daily, so the fast refresh a Tier A signal needs is
affordable. Billing per check instead would make the product's own SLA the most
expensive thing a customer could ask for.

**A source that errors is not a source that found nothing.** The same
distinction the data providers make. A quiet week and an outage look identical
in a count of detections and are opposite in what they require.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from typing import Any

from runtime.connectors.signalsource import (Detection, SignalSourceError, Subject,
                                             get_source)
from runtime.crypto import Keyring, open_sealed
from runtime.db import one
from zolts.signals import SignalDefinition, catalogue

# What one account-day of checking costs, per `docs/12`.
CHECK_KIND = "signal.check"


@dataclass(frozen=True)
class Watched:
    """What one pass over one signal did."""
    signal_key: str
    checked: int = 0
    detected: int = 0
    stale: int = 0
    enrolled: int = 0
    credits: float = 0.0
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {"signal": self.signal_key, "checked": self.checked,
                "detected": self.detected, "staleRefused": self.stale,
                "enrolled": self.enrolled, "credits": self.credits,
                "error": self.error}


@dataclass
class Pass:
    """Everything one run of the watcher did, per signal."""
    signals: list[Watched] = dc_field(default_factory=list)

    @property
    def credits(self) -> float:
        return round(sum(w.credits for w in self.signals), 4)

    def as_dict(self) -> dict[str, object]:
        return {"signals": [w.as_dict() for w in self.signals],
                "creditsBilled": self.credits,
                "note": "a check is billed once per account per day, however "
                        "many signals were looked for"}


def wanted(cur) -> set[str]:
    """The signal keys some live program is actually waiting on.

    Read from the published specs rather than from a separate list, because a
    separate list is a thing that goes out of date and then either watches for
    nothing or fails to watch for something a program needs.
    """
    cur.execute("select spec from program where status = 'live'")
    keys: set[str] = set()
    for row in cur.fetchall():
        trigger = (row["spec"] or {}).get("trigger") or {}
        for event in trigger.get("events") or []:
            if event.get("signal"):
                keys.add(event["signal"])
    return keys


def _due(cur, definition: SignalDefinition, now: datetime, limit: int) -> list[Subject]:
    """Accounts or people this signal has not looked at recently enough."""
    table = "account" if definition.entity == "account" else "person"
    cutoff = now - definition.refresh
    cur.execute(
        f"select e.* from {table} e"
        " where not exists ("
        "   select 1 from signal_check c"
        "    where c.entity_id = e.id and c.signal_key = %s and c.checked_at >= %s)"
        " order by e.created_at limit %s",
        (definition.key, cutoff, limit))
    return [Subject(entity_type=definition.entity, entity_id=str(row["id"]),
                    facts=dict(row)) for row in cur.fetchall()]


def _billed_today(cur, entity_id: str, now: datetime) -> bool:
    cur.execute(
        "select 1 from signal_check where entity_id = %s and billed"
        "   and (checked_at at time zone 'UTC')::date = (%s at time zone 'UTC')::date"
        " limit 1", (entity_id, now))
    return cur.fetchone() is not None


def _credential(cur, connector: str, secret_key: "str | Keyring | None") -> str | None:
    if not secret_key:
        return None
    cur.execute("select secret_enc from connection where provider = %s"
                " and status = 'active' order by updated_at desc limit 1",
                (connector,))
    row = one(cur)
    return open_sealed(row["secret_enc"], secret_key) if row else None


def _ingest(cur, tenant_id: str, definition: SignalDefinition,
            detection: Detection, now: datetime) -> int:
    """Hand a fresh detection to the enrollment path.

    The strength, the decay and the legal basis come from the definition rather
    than from the caller, which is the whole reason definitions exist: a signal
    ingested with somebody's guess at its half-life decays wrongly for as long
    as it lives.
    """
    from runtime.engine import enroll

    # The source's own confidence scales the strength. A feed that is 60% sure
    # it saw a funding round should not enroll as hard as one that is certain,
    # and `docs/06`'s decay function multiplies exactly this way.
    strength = max(0.0, min(1.0, definition.base_strength * detection.confidence))
    result = enroll.ingest(
        cur, tenant_id, entity_type=definition.entity,
        entity_id=detection.entity_id, type=definition.key, strength=strength,
        half_life_h=definition.half_life_h, source=definition.connector,
        legal_basis=definition.legal_basis, payload=detection.payload,
        observed_at=detection.observed_at,
        dedupe_key=detection.dedupe_key, now=now)
    return len(result.enrollments)


def once(cur, tenant: dict[str, Any], *, secret_key: "str | Keyring | None" = None,
         limit: int = 500, now: datetime | None = None,
         only: str | None = None) -> Pass:
    """One pass: look for every signal a live program wants, and act on it."""
    from runtime import metering

    moment = now or datetime.now(timezone.utc)
    tenant_id = str(tenant["id"])
    defined = catalogue()
    keys = wanted(cur)
    if only:
        keys = keys & {only}

    result = Pass()
    for key in sorted(keys):
        definition = defined.get(key)
        if definition is None:
            # A program triggering on a signal nobody defined. Reported rather
            # than skipped: it is a program that will never fire, and the
            # operator who published it believes it will.
            result.signals.append(Watched(
                signal_key=key,
                error="no definition in the catalogue, so nothing looks for it"))
            continue

        # Resolved before asking whether anything is due, so a connector nobody
        # registered is reported on the first pass rather than on the day the
        # first account arrives. A tenant with no accounts and a tenant with no
        # sources both look like a quiet pass otherwise, and only one of them
        # is a deployment somebody has to fix.
        try:
            source = get_source(definition.connector)
        except LookupError as exc:
            result.signals.append(Watched(signal_key=key, error=str(exc)))
            continue

        subjects = _due(cur, definition, moment, limit)
        if not subjects:
            result.signals.append(Watched(signal_key=key))
            continue

        try:
            detections = source.detect(
                key, subjects, credential=_credential(cur, definition.connector, secret_key),
                config=dict(definition.config or {}))
        except SignalSourceError as exc:
            # Nothing is recorded and nothing is billed. A source that is down
            # must not leave a trail of checks that found nothing, because that
            # trail is what the refresh clock and the hit history read.
            result.signals.append(Watched(signal_key=key, error=str(exc)))
            continue

        found = {d.entity_id: d for d in detections}
        checked = detected = stale = enrolled = 0
        credits = 0.0

        for subject in subjects:
            detection = found.get(subject.entity_id)
            is_stale = bool(detection and not definition.is_fresh(
                detection.observed_at, moment))

            bill = not _billed_today(cur, subject.entity_id, moment)
            cur.execute(
                "insert into signal_check (tenant_id, signal_key, entity_type,"
                " entity_id, detected, stale, checked_at, billed)"
                " values (%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant_id, key, definition.entity, subject.entity_id,
                 detection is not None, is_stale, moment, bill))
            checked += 1
            if bill:
                credits += float(metering.meter(cur, tenant, kind=CHECK_KIND,
                                                provider=definition.connector))

            if detection is None:
                continue
            if is_stale:
                stale += 1
                continue
            detected += 1
            enrolled += _ingest(cur, tenant_id, definition, detection, moment)

        result.signals.append(Watched(
            signal_key=key, checked=checked, detected=detected, stale=stale,
            enrolled=enrolled, credits=round(credits, 4)))
    return result


def latency(cur, program_id: str | None = None) -> dict[str, Any]:
    """Time to touch, measured from the signal rather than the enrollment.

    `docs/06` defines it as signal to action and the console measured it from
    the enrollment, which for a pushed signal is nearly the same instant and
    for a detected one is however long the source took to notice. Splitting it
    is more useful than either half: an operator whose p95 is bad needs to know
    whether to change source or to add workers.
    """
    cur.execute(
        "select"
        # observed -> ingested: how long the source took to notice.
        "  percentile_disc(0.95) within group ("
        "    order by extract(epoch from (s.ingested_at - s.observed_at)) / 60"
        "  ) as detection_p95,"
        # ingested -> sent: how long we took to act.
        "  percentile_disc(0.95) within group ("
        "    order by extract(epoch from (t.sent_at - s.ingested_at)) / 60"
        "  ) as execution_p95,"
        "  percentile_disc(0.95) within group ("
        "    order by extract(epoch from (t.sent_at - s.observed_at)) / 60"
        "  ) as total_p95,"
        "  count(*) as touches"
        "  from touch t"
        "  join enrollment e on e.id = t.enrollment_id"
        "  join signal s on s.id = (e.context->>'signal_id')::uuid"
        " where t.sent_at is not null"
        "   and (%s::uuid is null or e.program_id = %s::uuid)",
        (program_id, program_id))
    row = cur.fetchone()
    if not row or not row["touches"]:
        return {"touches": 0, "detectionP95Minutes": None,
                "executionP95Minutes": None, "totalP95Minutes": None}
    return {
        "touches": int(row["touches"]),
        "detectionP95Minutes": _minutes(row["detection_p95"]),
        "executionP95Minutes": _minutes(row["execution_p95"]),
        "totalP95Minutes": _minutes(row["total_p95"]),
    }


def _minutes(value: Any) -> int | None:
    return None if value is None else int(value)
