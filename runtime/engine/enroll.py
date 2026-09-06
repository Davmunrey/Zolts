"""Signal ingest through to enrollment.

This is where the holdout invariant is enforced. A control assignment enrolls
the entity and plans nothing: the enrollment exists so the measurement has a
denominator, and no action is ever queued against it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from runtime.engine import triggers
from runtime.repo import enrollments, ledger, programs, signals
from zolts import experiment, expr


class HoldoutMissing(ValueError):
    """A live program without a declared holdout cannot be run.

    Product invariant 4. A waiver is expressed in the program as an explicit
    holdout of zero with a stored justification, never by omitting the block.
    """


@dataclass(frozen=True)
class IngestResult:
    """What ingest did.

    `signal_id` is None when the dedupe key was already present. Callers need
    to tell that apart from "recorded, but nothing triggered": inferring it
    from an empty enrollment list reports every non-triggering signal as a
    duplicate, which tells a source its feed is being ignored when it is not.
    """
    signal_id: str | None
    enrollments: list["Enrolled"]

    @property
    def deduplicated(self) -> bool:
        return self.signal_id is None


@dataclass(frozen=True)
class Enrolled:
    enrollment_id: str
    program_key: str
    variant: str
    score: float | None
    tier: str | None
    reason: str


def holdout_pct(spec: dict[str, Any], program_key: str) -> float:
    block = spec.get("experiment")
    if not block or "holdout_pct" not in block:
        raise HoldoutMissing(f"program '{program_key}' declares no holdout")
    pct = float(block["holdout_pct"])
    if pct == 0 and not block.get("holdout_waiver_reason"):
        raise HoldoutMissing(
            f"program '{program_key}' waives its holdout with no written justification")
    return pct


def resolve_tier(spec: dict[str, Any], variables: dict[str, Any]) -> str | None:
    """First tier whose predicate holds, in declaration order."""
    for tier in (spec.get("route") or {}).get("tiers", []):
        when = tier.get("when")
        if when is None or expr.evaluate(when, variables):
            return tier.get("key")
    return None


def ingest(cur, tenant_id: str, *, entity_type: str, entity_id: str, type: str,
           strength: float, half_life_h: int, source: str, legal_basis: str,
           payload: dict[str, Any], observed_at: datetime,
           dedupe_key: str | None = None,
           score: float | None = None,
           now: datetime | None = None) -> IngestResult:
    """Record a signal and enroll it into every live program it triggers.

    Everything happens in the caller's transaction: a signal that is recorded
    but whose enrollment is lost would be silently un-actionable forever, since
    the dedupe key stops it being re-ingested.
    """
    now = now or datetime.now(timezone.utc)
    signal = signals.record(
        cur, tenant_id, entity_type=entity_type, entity_id=entity_id, type=type,
        strength=strength, half_life_h=half_life_h, source=source,
        legal_basis=legal_basis, payload=payload, observed_at=observed_at,
        dedupe_key=dedupe_key)
    if signal is None:
        # Already seen. A replay is not a new observation, and re-running the
        # trigger on it would enroll from history the caller already sent.
        return IngestResult(signal_id=None, enrollments=[])

    results: list[Enrolled] = []
    for program in programs.live(cur):
        spec = program["spec"]
        if type not in triggers.signal_types(spec):
            continue
        history = signals.within_window(
            cur, entity_id, triggers.signal_types(spec), triggers.window_start(spec, now))
        reason = triggers.matches(spec, signal, history)
        if reason is None:
            continue

        cooldown = triggers.cooldown_days(spec)
        if cooldown and enrollments.in_cooldown(cur, str(program["id"]), entity_id, cooldown):
            continue

        pct = holdout_pct(spec, program["key"])
        salt = (spec.get("experiment") or {}).get("salt", "")
        assignment = experiment.assign(str(entity_id), program["key"], pct, salt)
        variant = "control" if assignment.is_control else "treatment"

        effective_score = score if score is not None else float(strength) * 100
        floor = (spec.get("score") or {}).get("floor")
        if floor is not None and effective_score < float(floor):
            continue
        tier = resolve_tier(spec, {"score": effective_score, **(payload or {})})
        if tier is None:
            continue

        row = enrollments.enroll(
            cur, tenant_id, program_id=str(program["id"]), entity_type=entity_type,
            entity_id=entity_id, variant=variant, score=effective_score, tier=tier,
            state="active" if variant == "treatment" else "control",
            context={"reason": reason, "signal_id": str(signal["id"])},
            # A control enrollment is never scheduled, so the planner cannot
            # reach it even if a future caller forgets to check the variant.
            next_run_at=now if variant == "treatment" else None)
        if row is None:
            continue

        ledger.audit(cur, tenant_id, actor="engine", action="enrollment.created",
                     subject=str(row["id"]),
                     detail={"program": program["key"], "variant": variant,
                             "tier": tier, "reason": reason})
        results.append(Enrolled(str(row["id"]), program["key"], variant,
                                effective_score, tier, reason))
    return IngestResult(signal_id=str(signal["id"]), enrollments=results)
