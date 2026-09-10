"""Signal ingest through to enrollment.

This is where the holdout invariant is enforced. A control assignment enrolls
the entity and plans nothing: the enrollment exists so the measurement has a
denominator, and no action is ever queued against it.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from typing import Any

from runtime.engine import audience, triggers
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
    # Predicates this payload could not answer, in the sender's own terms.
    # Returned rather than only logged: the source that quotes a number is the
    # only party who can stop quoting it, and it is holding the response.
    warnings: list[str] = dc_field(default_factory=list)

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


#: The window `capacity_per_week` is counted over. Rolling rather than aligned
#: to a calendar week: a tenant declares no timezone, so an aligned week has no
#: anchor, and it would release the whole allowance in a burst every Monday.
CAPACITY_WINDOW_DAYS = 7


def resolve_tier(spec: dict[str, Any], variables: dict[str, Any],
                 occupancy: dict[str, int] | None = None) -> str | None:
    """The tier this account is routed to, or None when none will take it.

    In declaration order, the first tier whose predicate holds **and whose
    declared weekly capacity is not already spent**. `docs/04` has said since
    the first draft that human capacity is a finite resource and is modelled as
    one; it was not modelled at all, because `capacity_per_week` was read by
    nothing (D-81). The flagship programme caps t1 at 25 with the comment *the
    team's real human capacity*, and t1 of a 1:1 motion is the human-reviewed
    tier — `zolts/dsl.py` refuses a t1 play that auto-sends. Over-admitting to
    it fills a queue past what anybody can work, so proposals age out or are
    approved unread, which is the failure the propose-then-dispose invariant
    exists to prevent.

    **A full tier falls through to the next one the account also qualifies
    for**, rather than being refused. The account is still worked, by a cheaper
    play, which is what a routing capacity means; refusing would throw away a
    signal that was already paid for. Tiers are not experiment arms — the
    holdout is assigned by a hash of the entity, independently — so moving an
    account between tiers changes which play it gets and never which arm it is
    in. It falls out of the programme only when no tier it qualifies for has
    room.

    `occupancy` is how many enrolments each tier already holds in the window,
    and is omitted by callers with no database — `zolts.programtest` evaluates
    predicates alone, so a programme test reports the tier a predicate selects
    rather than the tier a loaded runtime would have room for.
    """
    for tier in (spec.get("route") or {}).get("tiers", []):
        when = tier.get("when")
        if when is not None and not expr.evaluate(when, variables):
            continue
        key = tier.get("key")
        cap = tier.get("capacity_per_week")
        if cap is not None and occupancy is not None and occupancy.get(key, 0) >= int(cap):
            continue
        return key
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
    warnings: list[str] = []
    for program in programs.live(cur):
        spec = program["spec"]
        if type not in triggers.signal_types(spec):
            continue
        history = signals.within_window(
            cur, entity_id, triggers.signal_types(spec), triggers.window_start(spec, now))
        unanswerable: list[triggers.Unanswerable] = []
        reason = triggers.matches(spec, signal, history, notes=unanswerable)
        if unanswerable:
            # A payload that carries the field and cannot answer the question:
            # a number sent as a string, a null where a figure goes. It is a
            # non-match, and it is reported twice — to the sender, in the
            # ingest response, and to the operator, here — because a source
            # sending the wrong type looks exactly like a source sending
            # nothing that matches, and the two need opposite responses. D-37.
            seen = {(note.clause, note.detail) for note in unanswerable}
            ledger.audit(cur, tenant_id, actor="engine", action="signal.unanswerable",
                         subject=str(program["id"]),
                         detail={"program": program["key"], "signal": type,
                                 "clauses": [{"where": clause, "error": detail}
                                             for clause, detail in sorted(seen)]})
            warnings.extend(
                f"program '{program['key']}' could not evaluate `{clause}`: {detail}"
                for clause, detail in sorted(seen))
        if reason is None:
            continue

        # Membership, before anything that costs money or contacts anybody.
        # The trigger says something happened; the audience says whether this
        # subject is one the program is for. Until this call existed the second
        # question was never asked, and a program's exclusions — a live
        # opportunity, the wrong segment, the wrong country — were decoration.
        try:
            if not audience.includes(cur, spec, program["key"], str(entity_id)):
                continue
        except audience.AudienceError as exc:
            # Fail closed, and say so. A program that has stopped enrolling
            # because its audience is broken looks exactly like a program with
            # no matching signals, and the difference is worth an audit row.
            ledger.audit(cur, tenant_id, actor="engine", action="enrollment.refused",
                         subject=str(program["id"]),
                         detail={"program": program["key"], "reason": str(exc),
                                 "entity_id": str(entity_id)})
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
        occupancy = enrollments.tier_counts_since(
            cur, str(program["id"]), CAPACITY_WINDOW_DAYS)
        tier = resolve_tier(spec, {"score": effective_score, **(payload or {})},
                            occupancy=occupancy)
        if tier is None:
            # Distinguished from *no tier matched*: the operator declared the
            # ceiling and is entitled to know it is what stopped this account,
            # rather than reading a silent non-enrolment as a scoring problem.
            if _would_have_matched(spec, {"score": effective_score, **(payload or {})}):
                ledger.audit(cur, tenant_id, actor="engine",
                             action="enrollment.at_capacity",
                             subject=str(program["id"]),
                             detail={"program": program["key"],
                                     "entity_id": str(entity_id),
                                     "occupancy": occupancy})
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
    return IngestResult(signal_id=str(signal["id"]), enrollments=results,
                        warnings=warnings)


def _would_have_matched(spec: dict[str, Any], variables: dict[str, Any]) -> bool:
    """Whether any tier's predicate held, ignoring capacity.

    The difference between *nothing wanted this account* and *everything that
    wanted it is full* is the difference between a scoring problem and a
    staffing one, and an operator reading a flat non-enrolment cannot tell.
    """
    return resolve_tier(spec, variables) is not None
