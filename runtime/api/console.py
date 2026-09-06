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

from runtime import fleet
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


def _unverified(cur, program_id: str) -> tuple[int, int]:
    """Conversions nobody read, and conversions in total.

    A reply that arrives without a body is a real event and tells us only that
    a human responded. It is still counted — flipping that would move every
    tenant's measured lift on a deploy, silently — so the share it represents
    is reported instead of hidden. See decision 16.
    """
    cur.execute(
        "select count(distinct o.enrollment_id) filter (where o.verified_by is null"
        "   and o.type = 'reply_positive') as unread,"
        " count(distinct o.enrollment_id) as total"
        " from enrollment e join outcome o on o.enrollment_id = e.id"
        " where e.program_id = %s and o.type = any(%s)",
        (program_id, CONVERSION_TYPES))
    row = cur.fetchone()
    return int(row["unread"] or 0), int(row["total"] or 0)


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


def _spend_and_latency(cur, program_id: str) -> tuple[float, int | None, int | None]:
    cur.execute("select coalesce(sum(cost_micros), 0) as micros from cost_event"
                " where program_id = %s", (program_id,))
    spend = int(cur.fetchone()["micros"]) / 1_000_000
    # Time to touch, in minutes, at the 95th percentile — measured from when
    # the signal happened, which is what `docs/06` means by it and what this
    # comment claimed while the query measured from the enrollment instead.
    #
    # For a signal a customer pushed those are nearly the same instant. For one
    # this runtime detected, the difference is however long the source took to
    # notice, and that half was invisible: a program looked fast because the
    # clock started after the slow part.
    #
    # A left join, because an enrollment with no signal behind it — a CRM sync,
    # a manual enrollment — still has a latency worth reporting, measured from
    # the only start it has.
    cur.execute(
        "select percentile_disc(0.95) within group ("
        "  order by extract(epoch from"
        "    (t.sent_at - coalesce(s.observed_at, e.entered_at))) / 60) as p95,"
        "  percentile_disc(0.95) within group ("
        "  order by extract(epoch from"
        "    (coalesce(s.ingested_at, e.entered_at) - coalesce(s.observed_at,"
        "     e.entered_at))) / 60) as detection_p95"
        " from touch t join enrollment e on e.id = t.enrollment_id"
        " left join signal s on s.id = (e.context->>'signal_id')::uuid"
        " where e.program_id = %s and t.sent_at is not null", (program_id,))
    row = cur.fetchone()
    p95 = row["p95"] if row and row["p95"] is not None else None
    detection = row["detection_p95"] if row and row["detection_p95"] is not None else None
    return (round(spend, 2), int(p95) if p95 is not None else None,
            int(detection) if detection is not None else None)


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
    spend, p95, detection_p95 = _spend_and_latency(cur, program_id)
    unread, converted_total = _unverified(cur, program_id)

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
        # The console could render a program and not address one: without an
        # id there is no button to activate a draft, which is the first thing a
        # partner must do after signup.
        "id": str(program["id"]),
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
        # How much of this number rests on replies nobody read. Reported rather
        # than corrected: correcting it silently would move every tenant's
        # measured lift on a deploy. Decision 16.
        "unverifiedConversions": unread,
        "conversions": converted_total,
        "unverifiedShare": (round(unread / converted_total, 3)
                            if converted_total else None),
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
        "p95": p95,
        # How much of the p95 was the source noticing rather than this runtime
        # acting. An operator whose latency is bad needs to know which of the
        # two to change, and they need opposite fixes.
        "detectionP95": detection_p95,
        "spend": spend, "pipeline": pipeline,
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


def prospects_view(cur, limit: int = 200) -> dict[str, Any]:
    """The accounts and contacts an operator works, and what is missing on them.

    There was no way to see this at all: `/v1/accounts` and `/v1/people` were
    POST-only, so a tenant could create prospects and never read one back, and
    the only way to enrich anything was the CLI.

    "Missing" is decided by `dataprovider.unresolved` — the same rule the
    enrichment path uses to decide whether to spend money. A screen with its
    own idea of what is missing is a screen that offers to buy a field the
    engine will then decline to buy.
    """
    from runtime.connectors.dataprovider import unresolved

    cur.execute(
        "select a.*, count(m.person_id) as contacts"
        "  from account a left join membership m on m.account_id = a.id"
        " group by a.id order by a.created_at desc limit %s", (limit,))
    accounts = [{
        "id": str(r["id"]), "name": r["name"], "domain": r["domain"],
        "country": r["country"], "employeeBand": r["employee_band"],
        "industry": r["industry_code"], "contacts": int(r["contacts"]),
        "missing": ["firmographics"] if unresolved(dict(r), "firmographics") else [],
    } for r in cur.fetchall()]

    cur.execute(
        "select p.*, a.name as account_name from person p"
        " left join membership m on m.person_id = p.id"
        " left join account a on a.id = m.account_id"
        " order by p.created_at desc limit %s", (limit,))
    people = []
    for row in cur.fetchall():
        record = dict(row)
        people.append({
            "id": str(row["id"]), "name": row["full_name"],
            "email": row["email"], "phone": row["phone"],
            "account": row["account_name"], "country": row["country"],
            "missing": [f for f in ("email", "phone") if unresolved(record, f)],
            # Whether this contact may be emailed at all, which is the first
            # thing an operator wants to know and the reason a bought address
            # is not automatically a usable one.
            "optedOut": bool(((row["consent_state"] or {}).get("email") or {})
                             .get("opted_out")),
        })

    return {
        "accounts": accounts, "people": people,
        "missingCounts": {
            "firmographics": sum(1 for a in accounts if a["missing"]),
            "email": sum(1 for p in people if "email" in p["missing"]),
            "phone": sum(1 for p in people if "phone" in p["missing"]),
        },
    }


def signals_view(cur, limit: int = 100) -> dict[str, Any]:
    """What fired, and how long it took to reach somebody.

    The latency split is the point rather than a detail: `docs/06` calls
    signal-to-action the highest-leverage variable in the system, and a bad
    number is either a source to change or workers to add.
    """
    from runtime import watch

    cur.execute(
        "select s.type, s.source, s.strength, s.observed_at, s.ingested_at,"
        "       coalesce(a.name, p.full_name) as subject"
        "  from signal s"
        "  left join account a on a.id = s.entity_id"
        "  left join person p on p.id = s.entity_id"
        " order by s.observed_at desc limit %s", (limit,))
    recent = [{
        "type": r["type"], "source": r["source"], "subject": r["subject"],
        "strength": round(float(r["strength"]), 3),
        "observedAt": r["observed_at"].isoformat(),
        "detectionMinutes": int(
            (r["ingested_at"] - r["observed_at"]).total_seconds() // 60),
    } for r in cur.fetchall()]

    cur.execute(
        "select signal_key, count(*) as checks,"
        "       count(*) filter (where detected) as detected,"
        "       count(*) filter (where stale) as stale,"
        "       max(checked_at) as last_checked"
        "  from signal_check group by signal_key order by signal_key")
    watched = [{
        "signal": r["signal_key"], "checks": int(r["checks"]),
        "detected": int(r["detected"]), "staleRefused": int(r["stale"]),
        "lastChecked": r["last_checked"].isoformat(),
    } for r in cur.fetchall()]

    return {"recent": recent, "watched": watched, "latency": watch.latency(cur)}


def spend_view(cur, tenant: dict[str, Any]) -> dict[str, Any]:
    """Credits, what is left, and where they went.

    `GET /v1/billing/current` has answered this since billing existed and no
    screen asked it, so the number a customer is charged on was visible only
    to whoever ran curl.
    """
    from runtime import metering

    period = metering.open_period(cur, tenant)
    budget = metering.allowance(cur, tenant)
    cur.execute(
        "select kind, sum(billed_credits) as credits, count(*) as events,"
        "       sum(cost_micros) as micros"
        "  from cost_event where billing_period_id = %s"
        " group by kind order by credits desc", (period["id"],))
    by_kind = [{"kind": r["kind"], "credits": float(r["credits"] or 0),
                "events": int(r["events"]),
                "costEur": round(int(r["micros"] or 0) / 1_000_000, 4)}
               for r in cur.fetchall()]

    return {
        "plan": tenant["plan"],
        "included": float(period["included_credits"]),
        "consumed": float(budget.consumed),
        "remaining": float(budget.remaining),
        "ceiling": float(budget.ceiling),
        "shareUsed": float(budget.share_used),
        "alerting": budget.alerting,
        "periodEnd": period["ends_at"].isoformat(),
        "byKind": by_kind,
    }


def review_view(cur, limit: int = 50) -> list[dict[str, Any]]:
    """What is waiting for a person, and what each gate said about it.

    Agents propose and the runtime disposes — and the disposing was `curl`.
    The rail counted a review queue that led nowhere, which makes product
    invariant 1 a claim with no surface behind it.

    The gate verdicts are read from the row rather than recomputed. Thresholds
    move; the question an audit asks is what was true when it was decided.
    """
    from runtime.repo import proposals

    out = []
    for row in proposals.queue(cur, limit):
        content = row["content"] or {}
        spend = row["spend"] or {}
        evaluation = row["eval"] or {}
        out.append({
            "id": str(row["id"]),
            "agent": row["agent"],
            "state": row["state"],
            "channel": row["channel"],
            "step": row["step_key"],
            "model": row["model"],
            "promptVersion": row["prompt_version"],
            "body": content.get("body") or "",
            # What provenance removed, so a reviewer sees the difference
            # between what the model wrote and what survived.
            "droppedClaims": content.get("dropped_claims") or [],
            "needsHumanReason": content.get("needs_human_reason"),
            "evidence": row["evidence"] or [],
            "evalScore": (float(row["eval_score"])
                          if row["eval_score"] is not None else None),
            "evalFailures": evaluation.get("failures") or [],
            "gateReason": row["gate_reason"],
            "spendVerdict": spend.get("verdict"),
            "costEur": round(int(row["cost_micros"] or 0) / 1_000_000, 4),
            "createdAt": row["created_at"],
        })
    return out


def build(cur, tenant: dict[str, Any]) -> dict[str, Any]:
    """The whole view model, from the tenant's own row.

    The row, not a description of it. The spend view reads the billing period,
    which is keyed by tenant id, and a caller that hands over a dictionary of
    labels gets a `KeyError` three frames down inside metering. Said here, it
    names the caller's mistake instead.
    """
    if not tenant.get("id"):
        raise ValueError(
            "console.build needs the tenant's own row, not a description of it: "
            "the spend view reads that tenant's billing period, which is keyed "
            "by id")
    live = programs.live(cur)
    cur.execute("select * from program where status <> 'live' order by key")
    other = [dict(r) for r in cur.fetchall()]

    # Drafts belong in the list, not only in a side panel of names.
    #
    # This rendered live programs alone, and signup publishes a tenant's
    # starter programs as drafts on purpose — so a partner who had just signed
    # up opened the console, saw nothing at all, and had no way to activate the
    # one thing they were given. The surface was built against a fixture in
    # which everything was already live, so nothing caught it.
    #
    # `plannedPrograms` keeps its meaning: what a blueprint promises and no
    # file implements. A draft this tenant actually holds is a program.
    drafts = [p for p in other if p["status"] in ("draft", "staged", "paused")]
    views = [program_view(cur, p) for p in live + drafts]
    decisions = decisions_view(cur)

    cur.execute("select count(*) as n from action where state in ('pending','leased')")
    queued = int(cur.fetchone()["n"])
    cur.execute("select count(*) as n from action where state = 'dead'")
    dead = int(cur.fetchone()["n"])
    # The review queue is drafts waiting for a person, not queued actions. A
    # rail counting actions told an operator there was work to review when
    # there was only work to send.
    review_items = review_view(cur)
    cur.execute("select count(*) as n from proposal where state in ('draft','needs_human')")
    review = int(cur.fetchone()["n"])
    cur.execute("select coalesce(sum(cost_micros),0) as m from cost_event where kind = 'llm'")
    llm_micros = int(cur.fetchone()["m"])

    return {
        # The one flag that separates the served console from the static build.
        # The static build inlines a fixture and keeps connect-src at 'none',
        # so its buttons must stay inert: a button that silently does nothing
        # is worse than no button, and both pages render from one file.
        "live": True,
        "note": f"Live data for {tenant['name']}.",
        "tenant": {"name": tenant["name"], "slug": tenant["slug"],
                   "region": tenant["region"], "blueprint": tenant["blueprint_id"]},
        "programs": views,
        "plannedPrograms": [{"key": p["key"], "version": p["version"],
                             "status": p["status"]}
                            for p in other if p not in drafts],
        "review": review_items,
        "decisions": decisions,
        "jurisdictions": sorted(decisions.keys()),
        "blueprints": [],
        "assignmentSample": [],
        "queue": {"pending": queued, "dead": dead, "review": review},
        # Capacity is reported whether or not it is managed. A fleet with no
        # members and a tenant whose provider owns the mailboxes look identical
        # in a summary and are opposite in consequence, so the surface has to
        # say which one this is.
        "fleet": fleet.health(cur),
        "prospects": prospects_view(cur),
        "signalsView": signals_view(cur),
        "spendView": spend_view(cur, tenant),
        "spend": {"llm_usd": round(llm_micros / 1_000_000, 4)},
    }
