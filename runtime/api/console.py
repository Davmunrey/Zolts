"""The console's view model, computed from live data.

The static build in `scripts/build_fixture.py` derives the same shape from the
repository's own artefacts with observed rates supplied by hand, because
nothing had run. This derives it from what has actually run, using the same
`zolts.experiment` and `zolts.policy` functions — one implementation of every
statistic, so the figure on screen cannot disagree with the runtime.

The shape is identical to the fixture's on purpose. The console does not know
whether it is looking at a demo or a tenant.
"""

from __future__ import annotations

import math
from typing import Any

from runtime.repo import enrollments, programs
from zolts.experiment import MIN_CONVERSIONS_PER_ARM, is_resolvable, lift, minimum_detectable_effect

# Outcomes that count as the primary conversion. A program declares its own
# primary metric; until outcome types are mapped per program this is the set
# the runtime records, and the mapping is named here rather than buried.
CONVERSION_TYPES = ["opp_created", "meeting", "reply_positive"]

AVG_OPPORTUNITY_EUR = 24_000

# The holdout range the console plots. Below 5 the estimate is unstable; above
# 25 the cost of the holdout exceeds what the precision buys.
HOLDOUT_CURVE = range(5, 26)


def _rates(cur, program_id: str) -> tuple[int, int, int, int]:
    counts = enrollments.variant_counts(cur, program_id)
    cur.execute(
        "select e.variant, count(distinct o.enrollment_id) as converted"
        " from enrollment e join outcome o on o.enrollment_id = e.id"
        " where e.program_id = %s and o.type = any(%s) group by e.variant",
        (program_id, CONVERSION_TYPES))
    converted = {r["variant"]: int(r["converted"]) for r in cur.fetchall()}
    return (counts.get("treatment", 0), counts.get("control", 0),
            converted.get("treatment", 0), converted.get("control", 0))


def _spend_and_latency(cur, program_id: str) -> tuple[float, int | None]:
    cur.execute("select coalesce(sum(cost_micros), 0) as micros from cost_event"
                " where program_id = %s", (program_id,))
    spend = int(cur.fetchone()["micros"]) / 1_000_000
    # Time to first touch, in minutes, at the 95th percentile. The signal-to-
    # touch SLA is the operational number a buyer checks first, so it is
    # measured rather than asserted.
    cur.execute(
        "select percentile_disc(0.95) within group ("
        "  order by extract(epoch from (t.sent_at - e.entered_at)) / 60) as p95"
        " from touch t join enrollment e on e.id = t.enrollment_id"
        " where e.program_id = %s and t.sent_at is not null", (program_id,))
    row = cur.fetchone()
    p95 = row["p95"] if row and row["p95"] is not None else None
    return round(spend, 2), int(p95) if p95 is not None else None


def _mde_curve(baseline: float, enrolled: int) -> dict[str, float]:
    """What effect each holdout size could detect, at this enrolled volume.

    Plotted because the honest answer to "why is my holdout so large" is a
    curve, not a policy.
    """
    curve: dict[str, float] = {}
    for pct in HOLDOUT_CURVE:
        control = int(enrolled * pct / 100)
        treatment = enrolled - control
        if control < 1 or treatment < 1:
            continue
        curve[str(pct)] = round(
            minimum_detectable_effect(baseline_rate=baseline, n_treatment=treatment,
                                      n_control=control) * 100, 2)
    return curve


def program_view(cur, program: dict[str, Any]) -> dict[str, Any]:
    spec = program["spec"]
    program_id = str(program["id"])
    n_treat, n_control, c_treat, c_control = _rates(cur, program_id)
    enrolled = n_treat + n_control
    treat_rate = c_treat / n_treat if n_treat else 0.0
    control_rate = c_control / n_control if n_control else 0.0
    experiment = spec.get("experiment") or {}
    holdout = float(experiment.get("holdout_pct", 0))

    # Enrolled in both arms is enough to show rates. Declaring an effect needs
    # a baseline the control arm actually establishes.
    measurable = n_treat > 0 and n_control > 0
    resolvable = measurable and is_resolvable(c_treat, c_control)
    baseline = max(control_rate, 0.01)
    mde = (minimum_detectable_effect(baseline_rate=baseline, n_treatment=n_treat,
                                     n_control=n_control) * 100) if resolvable else None
    movement = lift(treat_rate, control_rate) if measurable else {}
    abs_lift = round(movement.get("absolute", 0.0) * 100, 2) if measurable else None
    spend, p95 = _spend_and_latency(cur, program_id)

    # Pipeline is reported only when the lift clears the effect the sample can
    # detect. A number that reads as a result and is not one is the failure the
    # product exists to prevent, so it is withheld rather than caveated.
    significant = bool(resolvable and abs_lift is not None and abs_lift > mde)
    pipeline = (round(n_treat * (abs_lift / 100) * AVG_OPPORTUNITY_EUR)
                if significant and abs_lift else None)

    needed = None
    if resolvable and abs_lift is not None and not significant:
        for pct, value in _mde_curve(baseline, enrolled).items():
            if value < abs_lift:
                needed = int(pct)
                break

    metadata = program.get("metadata") or {}
    return {
        "key": program["key"], "version": program["version"],
        "blueprint": metadata.get("blueprint"),
        "status": program["status"], "holdout": holdout,
        "metric": experiment.get("primary_metric"),
        "signals": [e.get("signal") for e in (spec.get("trigger") or {}).get("events", [])],
        "tiers": [t.get("key") for t in (spec.get("route") or {}).get("tiers", [])],
        "autoSend": {k: bool(v.get("auto_send")) for k, v in (spec.get("plays") or {}).items()},
        "budget": (spec.get("budget") or {}).get("monthly_credits"),
        "specHash": program["spec_hash"],
        "name": metadata.get("name") or program["key"].replace("-", " ").capitalize(),
        "enrolled": enrolled, "nTreat": n_treat, "nControl": n_control,
        # Null, not zero. A program with no outcomes has no conversion rate,
        # and rendering one as 0.00% is a measurement the data does not carry.
        # The surface already treats null as "evidence appears once the control
        # group fills"; emitting zero routed it into the rendering path for a
        # real result and crashed on the absent lift.
        "treat": round(treat_rate * 100, 2) if measurable else None,
        "ctrl": round(control_rate * 100, 2) if measurable else None,
        "absLift": abs_lift,
        # Relative lift is infinite at a zero control rate, which is not a
        # number a screen can carry and not one JSON can encode.
        "relLift": (round(movement["relative"], 2)
                    if measurable and math.isfinite(movement.get("relative", math.inf))
                    else None),
        "mde": round(mde, 2) if mde is not None else None,
        "significant": significant, "neededHoldout": needed,
        # Named so the surface can say why rather than showing a silent dash.
        "unresolvedReason": None if resolvable else (
            "no enrollments" if not enrolled else
            "no control arm yet" if not n_control else
            f"fewer than {MIN_CONVERSIONS_PER_ARM} conversions in an arm; "
            "the baseline is not established"),
        "p95": p95, "spend": spend, "pipeline": pipeline,
        "mdeCurve": _mde_curve(baseline, enrolled) if enrolled else {},
    }


def decisions_view(cur, limit: int = 200) -> dict[str, list[dict[str, Any]]]:
    """Recent policy decisions, grouped by jurisdiction.

    The keys match the static fixture's exactly. The console does not know
    whether it is reading a demo or a tenant, and a second shape here would
    mean a second rendering path — which is how the trace came to show
    `undefined · undefined` the first time this ran against live data.
    """
    cur.execute(
        "select d.subject_id, d.action, d.decision, d.rule_key, d.jurisdiction,"
        " d.rationale, d.decided_at, p.full_name, p.email"
        " from policy_decision d left join person p on p.id = d.subject_id"
        " order by d.decided_at desc limit %s", (limit,))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in cur.fetchall():
        entry = {
            # The account is what an operator recognises; the subject id is not.
            "account": row["full_name"] or row["email"] or str(row["subject_id"])[:8],
            # The action is recorded as "<channel>.<verb>"; the trace shows the
            # channel, which is what the policy rule turns on.
            "channel": str(row["action"]).split(".")[0],
            "decision": row["decision"], "rule": row["rule_key"],
            "rationale": row["rationale"],
        }
        grouped.setdefault(row["jurisdiction"] or "—", []).append(entry)
    return grouped


def build(cur, tenant: dict[str, Any]) -> dict[str, Any]:
    live = programs.live(cur)
    cur.execute("select * from program where status <> 'live' order by key")
    other = [dict(r) for r in cur.fetchall()]
    views = [program_view(cur, p) for p in live]
    decisions = decisions_view(cur)

    cur.execute("select count(*) as n from action where state in ('pending','leased')")
    queued = int(cur.fetchone()["n"])
    cur.execute("select count(*) as n from action where state = 'dead'")
    dead = int(cur.fetchone()["n"])

    return {
        "note": f"Live data for {tenant['name']}.",
        "tenant": {"name": tenant["name"], "slug": tenant["slug"],
                   "region": tenant["region"], "blueprint": tenant["blueprint_id"]},
        "programs": views,
        "plannedPrograms": [{"key": p["key"], "version": p["version"],
                             "status": p["status"]} for p in other],
        "decisions": decisions,
        "jurisdictions": sorted(decisions.keys()),
        "blueprints": [],
        "assignmentSample": [],
        "queue": {"pending": queued, "dead": dead},
    }
