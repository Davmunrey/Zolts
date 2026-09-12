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

from datetime import datetime, timezone
from typing import Any

from runtime import fleet, outbox, sendingcontrol
from runtime.repo import enrollments, programs
from zolts import attention, billing, dsl
from zolts.deliverability import assess
from zolts.sendingcontrol import judge_resume
from zolts.catalog import load_catalog
from zolts.experiment import MIN_CONVERSIONS_PER_ARM, minimum_detectable_effect
from zolts.report import BASELINE_RATE_FLOOR, SIGNIFICANT, Comparison
from zolts import report as report_rules
from zolts import metrics

# Outcomes that count as the primary conversion: one definition, in
# `zolts.report`, shared with the measurement endpoint and the frozen report so
# the three cannot disagree about what converts. A list, because psycopg
# adapts a tuple as a row and a list as the array `= any(%s)` needs.
CONVERSION_TYPES = list(report_rules.CONVERSION_TYPES)

# The holdout range the console plots. Below 5 the estimate is unstable; above
# 25 the cost of the holdout exceeds what the precision buys.
HOLDOUT_CURVE = range(5, 26)


def _converted(cur, program_id: str, metric) -> dict[str, int]:
    """Enrollments per arm that produced the declared metric's event in time.

    Two clauses that were not here before (D-51): the event set comes from the
    programme's own `primary_metric`, and the outcome has to fall inside that
    metric's window from the moment the account entered. Without the second, a
    treatment arm enrolled in January is compared against a control arm still
    accumulating conversions in June.
    """
    cur.execute(
        "select e.variant, count(distinct o.enrollment_id) as converted"
        " from enrollment e join outcome o on o.enrollment_id = e.id"
        " where e.program_id = %s and o.type = any(%s)"
        "   and o.occurred_at < e.entered_at + make_interval(days => %s)"
        " group by e.variant",
        (program_id, list(metric.events), metric.window_days))
    return {r["variant"]: int(r["converted"]) for r in cur.fetchall()}


def _unverified(cur, program_id: str, metric) -> tuple[int, int]:
    """Conversions nobody read, and conversions in total.

    A reply that arrives without a body is a real event and tells us only that
    a human responded. It is still counted — flipping that would move every
    tenant's measured lift on a deploy, silently — so the share it represents
    is reported instead of hidden. See decision 16.

    Counted over the metric this programme is measured on and inside its
    window, because a share of a different denominator is not this figure's
    share (D-51). A programme measured on signed contracts has no unread
    conversions at all, which is the true answer rather than a flattering one.
    """
    cur.execute(
        "select count(distinct o.enrollment_id) filter (where o.verified_by is null"
        "   and o.type = 'reply_positive') as unread,"
        " count(distinct o.enrollment_id) as total"
        " from enrollment e join outcome o on o.enrollment_id = e.id"
        " where e.program_id = %s and o.type = any(%s)"
        "   and o.occurred_at < e.entered_at + make_interval(days => %s)",
        (program_id, list(metric.events), metric.window_days))
    row = cur.fetchone()
    return int(row["unread"] or 0), int(row["total"] or 0)


def _eur(micros: Any) -> float | None:
    """Micros to euros, to two decimals — the unit the panel is in.

    Rounded like the lift beside it (D-58): every figure on this panel reads
    to the same precision, or the reader has to work out which is which.
    """
    return None if micros is None else round(micros / 1_000_000, 2)


def _reports(cur, program_id: str) -> list[dict[str, Any]]:
    """The frozen incrementality reports for a program, newest period first.

    Read out of the stored canonical body rather than recomputed: the point of
    freezing one (ADR-043) is that the number a partner read in month three is
    the number this screen shows in month nine, and a surface that recomputed
    it would be a second answer with the same name.

    Six of them, which is two quarters of monthly periods — enough to see a
    verdict change, short enough that the panel stays a panel.
    """
    cur.execute(
        "select id, period_start, period_end, verdict, digest, body, frozen_at"
        " from incrementality_report where program_id = %s"
        " order by period_end desc, frozen_at desc limit 6", (program_id,))
    out = []
    for row in cur.fetchall():
        body = row["body"] or {}
        primary = body.get("primary") or {}
        pipeline = body.get("incremental_pipeline_micros")
        baseline = body.get("baseline") or {}
        out.append({
            "id": str(row["id"]),
            "periodStart": row["period_start"].isoformat(),
            "periodEnd": row["period_end"].isoformat(),
            "verdict": row["verdict"],
            "digest": row["digest"],
            # Percentage points, the unit the rest of this panel is in.
            "liftPp": (None if primary.get("lift") is None
                       else round(primary["lift"] * 100, 2)),
            "mdePp": (None if primary.get("minimum_detectable_effect") is None
                      else round(primary["minimum_detectable_effect"] * 100, 2)),
            "nTreat": primary.get("treatment_enrolled", 0),
            "nControl": primary.get("control_enrolled", 0),
            "incremental": primary.get("incremental_conversions"),
            # Euros, because the report holds micros and no screen reads micros.
            "pipelineEur": None if pipeline is None else round(pipeline / 1_000_000),
            # Acquisition cost against the ceiling the programme declared
            # (decision 45). Reported beside lift and never acted on: a
            # programme is over its cost per meeting every day until the first
            # one lands, so a stop here would kill programmes that are working.
            "costPerMeetingEur": _eur(body.get("cost_per_incremental_meeting_micros")),
            "costPerMeetingCeilingEur": _eur(body.get("max_cost_per_meeting_micros")),
            "costPerMeetingWithheld": body.get("cost_per_meeting_withheld_because"),
            "overCostCeiling": body.get("over_cost_per_meeting_ceiling"),
            # What the tenant's own spend rests on: `declared` for this period,
            # or `run rate` prorated from onboarding. The same number means
            # different things, and a reader cannot tell by looking at it
            # (decision 46).
            "ownSpendBasis": body.get("own_spend_basis"),
            "ownSpendEur": _eur(body.get("own_spend_micros")),
            # Why there is no figure, in the report's own words. A dash with no
            # reason is what made the live console's first measurement panel
            # unreadable (D-28).
            "withheld": body.get("pipeline_withheld_because"),
            "credits": body.get("credits_total", 0),
            "unreadShare": body.get("unread_share"),
            "baselineDigest": baseline.get("digest"),
            "frozenAt": row["frozen_at"].isoformat(),
        })
    return out


def _rates(cur, program_id: str, metric) -> tuple[int, int, int, int]:
    counts = enrollments.variant_counts(cur, program_id)
    converted = _converted(cur, program_id, metric)
    return (counts.get("treatment", 0), counts.get("control", 0),
            converted.get("treatment", 0), converted.get("control", 0))


def _opportunity_conversions(cur, program_id: str, window_days: int) -> tuple[int, int]:
    """Enrollments in each arm that produced an opportunity.

    Separate from `_rates` because euros come from deals: a lift measured over
    every conversion type is mostly positive replies, and multiplying that by a
    deal size prices a reply as a deal (D-49).
    """
    cur.execute(
        "select e.variant, count(distinct o.enrollment_id) as converted"
        " from enrollment e join outcome o on o.enrollment_id = e.id"
        " where e.program_id = %s and o.type = 'opp_created'"
        "   and o.occurred_at < e.entered_at + make_interval(days => %s)"
        " group by e.variant",
        (program_id, window_days))
    converted = {r["variant"]: int(r["converted"]) for r in cur.fetchall()}
    return converted.get("treatment", 0), converted.get("control", 0)


def _deal_value(cur) -> tuple[int | None, int]:
    """The tenant's own average deal amount, in micros, and what it averages.

    Theirs, not ours, and no longer a constant in this file. A runtime that
    carried an average deal size priced every tenant's pipeline at a number
    somebody here chose — €24,000, a mid-market B2B SaaS figure, applied to an
    ecommerce reorder as readily as to an enterprise contract (D-49). The demo
    declares its own assumption in `scripts/seed_demo.py`; a tenant with no
    amounts synced gets no figure and the reason why.
    """
    cur.execute("select avg(amount_micros)::bigint as average, count(*) as n"
                " from opportunity where amount_micros is not null and amount_micros > 0")
    row = cur.fetchone()
    n = int(row["n"] or 0)
    return (int(row["average"]) if n else None), n


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
    experiment_spec = spec.get("experiment") or {}
    # What this programme says it measures. Unknown names are refused at
    # admission, so a stored programme always resolves; a spec that predates
    # the registry falls back to the default set (D-51).
    try:
        metric = metrics.resolve(experiment_spec.get("primary_metric"))
    except metrics.MetricError:
        metric = metrics.DEFAULT
    n_treat, n_control, c_treat, c_control = _rates(cur, program_id, metric)
    enrolled = n_treat + n_control
    treat_rate = c_treat / n_treat if n_treat else 0.0
    control_rate = c_control / n_control if n_control else 0.0
    experiment = experiment_spec
    holdout = float(experiment.get("holdout_pct", 0))

    # Enrolled in both arms is enough to show rates. Declaring an effect needs
    # a baseline the control arm actually establishes — and that judgement is
    # `zolts.report.Comparison`, the same object the frozen report is made of,
    # so this screen and the signed document cannot reach different verdicts
    # about the same two arms (ADR-043).
    measurable = n_treat > 0 and n_control > 0
    primary = Comparison(treatment_enrolled=n_treat, control_enrolled=n_control,
                         treatment_converted=c_treat, control_converted=c_control)
    resolvable = primary.resolvable
    baseline = max(control_rate, BASELINE_RATE_FLOOR)
    mde = (None if primary.minimum_detectable_effect is None
           else primary.minimum_detectable_effect * 100)
    abs_lift = round((primary.lift or 0.0) * 100, 2) if measurable else None
    spend, p95, detection_p95 = _spend_and_latency(cur, program_id)
    unread, converted_total = _unverified(cur, program_id, metric)

    # Pipeline is euros, and euros come from deals. It multiplied the lift in
    # *conversions* — a set that is mostly positive replies — by an assumed
    # deal size, so a reply counted as an opportunity and the figure overstated
    # by the whole reply-to-opportunity ratio (D-49). It now rests on the
    # opportunity comparison and on the tenant's own average deal amount, and
    # says why when it rests on neither.
    o_treat, o_control = _opportunity_conversions(cur, program_id, metric.window_days)
    opportunities = Comparison(treatment_enrolled=n_treat, control_enrolled=n_control,
                               treatment_converted=o_treat, control_converted=o_control)
    average_micros, with_amount = _deal_value(cur)
    increment = opportunities.incremental_conversions
    significant = primary.verdict == SIGNIFICANT
    pipeline = (None if increment is None or average_micros is None
                else round(increment * average_micros / 1_000_000))
    withheld = None
    if pipeline is None:
        withheld = ("the CRM holds no opportunity with an amount"
                    if increment is not None else
                    f"the opportunity comparison is {opportunities.verdict}")

    needed = None
    if resolvable and abs_lift is not None and not significant:
        for pct, value in _mde_curve(baseline, enrolled).items():
            # `<` rather than `<=`: an effect exactly equal to the detectable
            # one is at the threshold, not past it, and the conservative
            # reading is that more enrolment is still needed. A mutation-
            # coverage run flagged `<=` as unkilled, which is the honest
            # reading of a strict comparison between two computed floats:
            # equality would need `_mde_curve` to land on the observed lift to
            # the last bit. Annotated rather than answered with a test that
            # asserts a no-op.
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
        # What that name counts, in words, and how long it counts for. The
        # name was on screen and nothing measured by it (D-51).
        "metricCounts": metric.describes,
        "metricWindowDays": metric.window_days,
        "metricTestsValue": metric.kind == metrics.VALUE,
        "signals": [e.get("signal") for e in (spec.get("trigger") or {}).get("events", [])],
        "tiers": [t.get("key") for t in (spec.get("route") or {}).get("tiers", [])],
        "autoSend": {k: bool(v.get("auto_send")) for k, v in (spec.get("plays") or {}).items()},
        "budget": (spec.get("budget") or {}).get("monthly_credits"),
        "specHash": program["spec_hash"],
        # The document itself. A console that edits a program has to emit a
        # whole document, because that is what `POST /v1/programs` validates
        # and versions — a patch would make the engine the author.
        "spec": spec,
        "meta": metadata,
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
        "relLift": (round(primary.treatment_rate / primary.control_rate, 2)
                    if measurable and primary.control_rate else None),
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
        # Euros against the holdout, and what they rest on: how many
        # opportunities the holdout says would not have existed, and the
        # tenant's own average deal amount rather than a constant. Null with a
        # reason beside it, never a silent dash (D-28).
        "pipelineWithheld": withheld,
        "incrementalOpportunities": increment,
        "oppTreat": o_treat, "oppControl": o_control,
        "avgOpportunityEur": (None if average_micros is None
                              else round(average_micros / 1_000_000)),
        "opportunitiesWithAmount": with_amount,
        "mdeCurve": _mde_curve(baseline, enrolled) if enrolled else {},
        # The signed artefact, beside the live figure it was frozen from. The
        # CFO surface `docs/17` asks for is derived from what the operator
        # already produced and asks the customer for nothing (ADR-043).
        "reports": _reports(cur, program_id),
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


def policy_view(cur, limit: int = 200) -> dict[str, Any]:
    """What the policy engine decided, and which rule decided it.

    A regulated buyer's first question is not whether there is a policy engine;
    it is to be shown a denial and told which rule produced it. That answer
    existed at `GET /v1/decisions` from the day the gate did, and no screen
    asked for it — so the demonstration required a terminal.

    Grouped by rule rather than by contact. One contact denied once is a
    correct denial; one rule denying four fifths of a program is a program to
    fix, and only the second grouping shows it.
    """
    cur.execute(
        "select d.decision, d.rule_key, d.jurisdiction, d.action, d.rationale,"
        "       d.pack_version, d.pack_digest,"
        "       d.decided_at, coalesce(p.full_name, p.email::text) as subject"
        "  from policy_decision d"
        "  left join person p on p.id = d.subject_id"
        " order by d.decided_at desc limit %s", (limit,))
    recent = [{
        "decision": r["decision"], "rule": r["rule_key"],
        "jurisdiction": r["jurisdiction"] or "—",
        # The rules that decided, not the rules in today's release.
        "pack": (r["pack_digest"] or "")[:12] or "—",
        "channel": str(r["action"]).split(".")[0],
        "subject": r["subject"] or "—",
        "rationale": r["rationale"],
        "decidedAt": r["decided_at"].isoformat(),
    } for r in cur.fetchall()]

    cur.execute(
        "select rule_key, decision, count(*) as n from policy_decision"
        " group by rule_key, decision order by n desc")
    by_rule: dict[str, dict[str, Any]] = {}
    for row in cur.fetchall():
        entry = by_rule.setdefault(row["rule_key"], {"rule": row["rule_key"],
                                                     "allow": 0, "deny": 0})
        entry["allow" if row["decision"] == "allow" else "deny"] += int(row["n"])

    allowed = sum(e["allow"] for e in by_rule.values())
    denied = sum(e["deny"] for e in by_rule.values())
    # Which rules decided, and how many decisions cannot say. A decision with
    # no pack is one taken before the packs were versioned (D-53); it is
    # reported rather than hidden, because "we cannot tell you which rule"
    # is the honest answer and a screen that omitted it would imply otherwise.
    cur.execute("select pack_version, pack_digest, count(*) as n from policy_decision"
                " group by pack_version, pack_digest order by n desc")
    packs = [{"version": r["pack_version"], "digest": r["pack_digest"], "decisions": int(r["n"])}
             for r in cur.fetchall()]
    unattributed = sum(p["decisions"] for p in packs if not p["digest"])

    return {
        "recent": recent,
        "byRule": sorted(by_rule.values(), key=lambda e: -(e["deny"] + e["allow"])),
        "packs": [p for p in packs if p["digest"]],
        "decisionsWithNoPack": unattributed,
        "totals": {"allow": allowed, "deny": denied,
                   # The share of work the engine stopped. A tenant at zero has
                   # either a clean list or a policy that is not running, and
                   # those look identical in a count of sends.
                   "denyRate": round(denied / (allowed + denied), 4)
                   if allowed + denied else None},
        "jurisdictions": sorted({r["jurisdiction"] for r in recent}),
    }


def audit_view(cur, limit: int = 200) -> dict[str, Any]:
    """Who did what, and when.

    Written on every key issued, rotated or revoked, every program published or
    activated, and every proposal a person approved. It answers the question an
    audit actually asks — not what the system did, but which human authorised
    it — and until now the only way to read it was a SQL prompt.
    """
    cur.execute(
        "select actor, action, subject, detail, at from audit_log"
        " order by at desc limit %s", (limit,))
    entries = [{
        # The key id, not the token, and never the token: keys are shown once
        # at creation and this table is read by people who did not create them.
        "actor": r["actor"], "action": r["action"],
        "subject": (str(r["subject"])[:8] if r["subject"] else "—"),
        "detail": r["detail"] or {},
        "at": r["at"].isoformat(),
    } for r in cur.fetchall()]

    cur.execute("select action, count(*) as n from audit_log"
                " group by action order by n desc limit 20")
    return {"entries": entries,
            "byAction": [{"action": r["action"], "count": int(r["n"])}
                         for r in cur.fetchall()],
            "actors": sorted({e["actor"] for e in entries})}


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

    # The dossier's state comes with the account rather than in a second pass:
    # an operator picking accounts to research needs to see which ones already
    # have one, and a screen that makes them click to find out is a screen that
    # buys the same twenty-credit document twice.
    cur.execute(
        "select a.*, count(m.person_id) as contacts, d.state as dossier_state,"
        "       d.built_through as dossier_through"
        "  from account a"
        "  left join membership m on m.account_id = a.id"
        "  left join lateral ("
        "    select state, built_through from dossier"
        "     where account_id = a.id order by seq desc limit 1) d on true"
        " group by a.id, d.state, d.built_through"
        " order by a.created_at desc limit %s", (limit,))
    accounts = [{
        "id": str(r["id"]), "name": r["name"], "domain": r["domain"],
        "country": r["country"], "employeeBand": r["employee_band"],
        "industry": r["industry_code"], "contacts": int(r["contacts"]),
        "missing": ["firmographics"] if unresolved(dict(r), "firmographics") else [],
        "dossier": r["dossier_state"],
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

    from runtime import research

    return {
        "accounts": accounts, "people": people,
        # Coverage and staleness together, because coverage alone lies: a
        # dossier on every account, all written before this quarter's news, is
        # 100% coverage and no knowledge.
        "research": research.coverage(cur),
        "missingCounts": {
            "firmographics": sum(1 for a in accounts if a["missing"]),
            "email": sum(1 for p in people if "email" in p["missing"]),
            "phone": sum(1 for p in people if "phone" in p["missing"]),
        },
        # What each field costs, read out of `zolts.billing` rather than
        # restated. A screen that spends a data budget without showing the
        # price is how the budget disappears, and a screen that quotes its own
        # copy of the price list quotes a stale one the day the list changes.
        # The spread is the reason it has to be visible: a phone number is
        # three times an email.
        "prices": {
            **{field: float(billing.CREDITS[f"enrich.{field}"])
               for field in ("email", "phone", "firmographics")},
            # The surface printed "20 credits" for a dossier as a literal, one
            # panel below the fields it now prices properly. A price restated
            # in markup is a price that is wrong the day the list changes, and
            # nothing would have failed.
            "dossier": float(billing.CREDITS["agent.dossier"]),
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

    # The number and the verdict on it. `docs/06` publishes a p95 target per
    # tier and calls it the contractual SLA of two plans; until now the
    # console printed the measurement beside no target at all, which is an
    # SLA an operator can miss for a quarter without being told (D-97).
    # Which signals earn their keep (docs/28, OX-3): one funnel per catalogue
    # signal, the silent ones included, with a conversion counted only inside
    # the window the programme declared. On this view rather than fetched on
    # demand because it is per tenant, not per row, and it is the reason an
    # operator opens this screen after the first month.
    from runtime import signalfunnel

    return {"recent": recent, "watched": watched,
            "latency": watch.latency(cur), "sla": watch.sla(cur),
            "funnel": signalfunnel.for_tenant(cur)}


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
        # The margin number of the agent layer. `docs/08` targets under two
        # cents of tokens per contact reached and nothing computed it, so a
        # product reaching a contact for two cents and one reaching them for
        # twenty looked identical on every screen (AGENT-4). Counted over this
        # period, so both halves move together.
        "costPerContact": metering.cost_per_contact(
            cur, since=period["starts_at"], until=period["ends_at"]),
    }


def with_resumption(cur, health: dict[str, Any]) -> dict[str, Any]:
    """Say what lifting each pause would be, before anybody presses it.

    `fleet.load` drops a paused domain, so the fleet view could report that a
    domain was paused and nothing at all about whether the cause was still
    true. An operator lifting a cut-off could not tell, from the screen, the
    difference between agreeing with the measurement and overriding a live one
    that would take the domain back on the next reputation event.

    Computed here rather than in `runtime/fleet`, which `runtime/sendingcontrol`
    imports: putting it there would close an import cycle. Read-only, and it
    reuses the same two functions the act itself uses, so the sentence on the
    screen and the verdict written into the audit row cannot disagree.
    """
    for paused in (health.get("pausedDomains") or []):
        verdict = assess(sendingcontrol.domain_metrics(cur, paused["name"]))
        paused["resumption"] = judge_resume(verdict).value
        paused["rule"] = verdict.rule_key
        paused["rationale"] = verdict.rationale
    return health


def attention_view(*, programs, queue, fleet_health, tasks, spend,
                   signals) -> dict[str, Any]:
    """What needs a person, from the judgements the other views already made.

    Built from the rendered views rather than from its own queries, on purpose.
    A home screen that counts the review queue with a second query is a second
    answer waiting to disagree with the first, and the day they diverge the
    operator believes the summary — that is how a dashboard starts lying.

    Every item carries three things: what it is, what ignoring it costs, and
    which screen the action lives on. A worklist without the cost is a to-do
    list somebody else wrote, and an operator reading one has no way to decide
    what to skip.
    """
    items: list[dict[str, Any]] = []

    def add(kind: str, title: str, detail: str, count: int = 1, view: str | None = None):
        rule = attention.kind(kind)
        items.append({
            "kind": kind, "title": title, "detail": detail, "count": count,
            "urgency": rule.urgency.value, "view": view or rule.view,
            "cost": rule.cost,
        })

    # Irreversible first. A burned domain is not undone by acting tomorrow.
    for domain in (fleet_health.get("domains") or []):
        alarmed = [b for b in domain.get("mailboxes", [])
                   if b.get("health") == "alarm"]
        if domain.get("health") == "alarm" or alarmed:
            add("sending.alarm", f"{domain['name']} is in alarm",
                domain.get("rationale") or f"{len(alarmed)} mailbox(es) in alarm",
                count=max(1, len(alarmed)))
    for paused in (fleet_health.get("pausedDomains") or []):
        add("sending.alarm", f"{paused['name']} is paused",
            paused.get("pausedReason") or "paused, reason not recorded")

    if queue.get("dead"):
        add("outbox.dead", "Actions gave up after their retries",
            "Nothing will deliver them and nothing else will notice",
            count=int(queue["dead"]))

    counts = tasks.get("counts") or {}
    if counts.get("overdue"):
        add("task.overdue", "Human tasks past their deadline",
            "The SLA the step stamped has already passed",
            count=int(counts["overdue"]))

    if queue.get("review"):
        add("review.waiting", "Drafts waiting for a person",
            "A gate declined to send them unattended",
            count=int(queue["review"]))

    if spend and spend.get("alerting"):
        share = round((spend.get("shareUsed") or 0) * 100)
        add("spend.ceiling", f"{share}% of the ceiling used",
            f"{spend.get('remaining', 0):,.0f} credits left this period")

    for row in (signals.get("sla") or []):
        missed = [name for name, stage in (row.get("stages") or {}).items()
                  if stage.get("verdict") == "misses"]
        if missed:
            add("sla.missed", f"Tier {row['tier']} is missing its time-to-touch SLA",
                "Missing: " + ", ".join(sorted(missed)), count=len(missed))

    open_not_late = int(counts.get("open", 0)) - int(counts.get("overdue", 0))
    if open_not_late > 0:
        add("task.due", "Human tasks waiting", "Still inside the SLA they declared",
            count=open_not_late)

    cost = (spend or {}).get("costPerContact") or {}
    if cost.get("verdict") == "misses":
        add("cost.over", "Cost per contact is over target",
            f"€{cost['eurPerContact']:.4f} against €"
            f"{cost['targetEurPerContact']:.2f} in tokens")

    drafts = [p for p in programs if p.get("status") in ("draft", "staged")]
    if drafts:
        add("program.draft", "Programmes published and never activated",
            ", ".join(sorted(p["key"] for p in drafts))[:120], count=len(drafts))

    ranked = attention.rank(items)
    return {
        "items": ranked,
        "counts": attention.urgency_counts(ranked),
        # What the runtime is doing unattended, so an empty worklist reads as
        # working rather than as broken. An empty state that says nothing is
        # indistinguishable from a screen that failed to load.
        "unattended": {
            "queued": int(queue.get("pending") or 0),
            "watching": len(signals.get("watched") or []),
            "live": len([p for p in programs if p.get("status") == "live"]),
        },
    }


def tasks_view(cur, limit: int = 100) -> dict[str, Any]:
    """The work waiting for a person, and what is late.

    `GET /v1/tasks` has answered this since D-83 gave a human task a way to be
    closed, and no screen asked it. A queue an operator cannot see is a queue
    nobody works, and the SLA `docs/09` gives the task channel is then a
    deadline measured against nothing — the shape this repository keeps
    finding, on the one surface where the person doing the work sits.

    The deadline was stamped when the task was created, from the step's own
    `sla_hours`, so republishing the programme with a longer SLA does not make
    a late task punctual. *Late* is computed here rather than stored: a stored
    copy of a comparison is a second answer waiting to disagree with the two
    timestamps beside it.
    """
    from runtime.repo import tasks as tasks_repo

    now = datetime.now(timezone.utc)
    rows = []
    for row in tasks_repo.open_tasks(cur, limit):
        due = row.get("due_at")
        rows.append({
            "id": str(row["id"]),
            "channel": row["channel"],
            "step": row["step_key"],
            "program": row.get("program_key"),
            "createdAt": row["created_at"].isoformat(),
            "dueAt": due.isoformat() if due else None,
            # Minutes rather than a timestamp, because the question is how late
            # rather than when: negative is time left, positive is time past.
            "lateMinutes": (int((now - due).total_seconds() // 60) if due else None),
            "late": bool(due and now > due),
        })
    return {"tasks": rows, "counts": tasks_repo.counts(cur)}


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

    # Computed once and shared with the worklist below. Two calls would be two
    # answers, and the one an operator reads first is the summary.
    fleet_health = with_resumption(cur, fleet.health(cur))
    signals = signals_view(cur)
    tasks = tasks_view(cur)
    spend = spend_view(cur, tenant)
    # Named for the view rather than `dead`, which is already the queue's
    # count of them in this scope. A shadowed name here is a dict where an
    # integer was expected, three lines from where anybody would look.
    dead_view = outbox.dead(cur)

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
        # The blueprint catalogue's own gaps: plays a blueprint promises
        # and no program file implements. Read from `zolts.catalog` so the
        # demo and a tenant render one section from one rule.
        "catalogueGaps": load_catalog().gaps(),
        # What a console may tune, carrying the schema's own bounds. Derived
        # rather than listed here: a control that restated a range would go on
        # offering it after the schema moved.
        "controls": dsl.controls(),
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
        "fleet": fleet_health,
        "prospects": prospects_view(cur),
        "signalsView": signals,
        "tasksView": tasks,
        "outboxView": dead_view,
        "spendView": spend,
        "policyView": policy_view(cur),
        "auditView": audit_view(cur),
        "spend": {"llm_usd": round(llm_micros / 1_000_000, 4)},
        # The worklist, built from the views above rather than from queries of
        # its own, so the home screen and the screen it links to cannot
        # disagree about what is waiting.
        "attention": attention_view(
            programs=views,
            queue={"pending": queued, "dead": dead, "review": review},
            fleet_health=fleet_health, tasks=tasks, spend=spend, signals=signals),
    }
