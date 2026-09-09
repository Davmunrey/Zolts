"""Compose and freeze the incrementality report from what the runtime recorded.

The report is derived from data the operator already generates — enrollments,
outcomes, touches, policy decisions, cost events, the CRM's deals and the
frozen baseline — with no input of its own. `docs/17`'s guardrail for the
CFO surface: the moment it needs somebody to enter something, it has become a
second product.

Frozen at the close of a billing period, in the same transaction as the
statement, because that is when the period's costs stop moving. Cumulative
from the program's first enrollment, so a pilot is one comparison rather than
three too small to resolve, and nothing here compares one period with another
(`docs/10`: only concurrent control comparisons are reported).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from runtime.repo import baseline as baseline_repo
from runtime.repo import period_spend as period_spend_repo
from runtime.repo import ledger, reports
from zolts import metrics
from zolts.report import (BaselineQuote, Comparison, IncrementalityReport,
                          ReportError, ceiling_micros)

# The statuses a touch has once a provider accepted it. `queued` and `failed`
# are attempts, not touches a person received.
SENT_STATUSES = ("sent", "delivered", "opened", "replied", "bounced")


def compose(cur, program: dict[str, Any], period: dict[str, Any]) -> IncrementalityReport:
    """The report for one program as of the end of one billing period.

    Everything is bounded by the period's end and nothing by its start: the
    arms are every enrollment that entered before the end, and every outcome
    those enrollments produced before it. A late outcome recorded after the
    close, dated inside the window, is in the next period's report; the frozen
    one says what was known when it was frozen.
    """
    program_id = str(program["id"])
    end: datetime = period["ends_at"]
    spec = program.get("spec") or {}
    experiment = spec.get("experiment") or {}
    holdout = float(experiment.get("holdout_pct", 0))
    # The metric this programme declared, and the window it declared it in.
    # Every report used to count the same three outcome types, at any time
    # after enrolment, whatever the programme said it measured (D-51).
    try:
        metric = metrics.resolve(experiment.get("primary_metric"))
    except metrics.MetricError:
        metric = metrics.DEFAULT

    cur.execute("select min(entered_at) as first from enrollment"
                " where program_id = %s and entered_at < %s", (program_id, end))
    first = cur.fetchone()["first"]
    start: datetime = first or period["starts_at"]

    cur.execute("select variant, count(*) as n from enrollment"
                " where program_id = %s and entered_at < %s group by variant",
                (program_id, end))
    enrolled = {r["variant"]: int(r["n"]) for r in cur.fetchall()}

    # Inside the metric window, like the primary number this composes and like
    # the opportunity arms below. It was not, and that was tolerable while the
    # breakdown only disclosed what the primary number was made of. The meeting
    # arm is now divided into money (decision 45), and a comparison counted over
    # a different span from the one it is set beside is two measurements sharing
    # a document.
    cur.execute(
        "select e.variant, o.type, count(distinct o.enrollment_id) as n"
        " from enrollment e join outcome o on o.enrollment_id = e.id"
        " where e.program_id = %s and e.entered_at < %s and o.occurred_at < %s"
        "   and o.type = any(%s)"
        "   and o.occurred_at < e.entered_at + make_interval(days => %s)"
        " group by e.variant, o.type",
        (program_id, end, end, list(metrics.OUTCOME_TYPES), metric.window_days))
    by_type: dict[str, dict[str, int]] = {}
    for r in cur.fetchall():
        by_type.setdefault(r["type"], {})[r["variant"]] = int(r["n"])

    cur.execute(
        "select e.variant, count(distinct o.enrollment_id) as converted,"
        " count(distinct o.enrollment_id) filter (where o.verified_by is null"
        "   and o.type = 'reply_positive') as unread"
        " from enrollment e join outcome o on o.enrollment_id = e.id"
        " where e.program_id = %s and e.entered_at < %s and o.occurred_at < %s"
        "   and o.type = any(%s)"
        "   and o.occurred_at < e.entered_at + make_interval(days => %s)"
        " group by e.variant",
        (program_id, end, end, list(metric.events), metric.window_days))
    converted, unread = {}, 0
    for r in cur.fetchall():
        converted[r["variant"]] = int(r["converted"])
        unread += int(r["unread"])

    cur.execute(
        "select count(*) as n from touch t join enrollment e on e.id = t.enrollment_id"
        " where e.program_id = %s and t.direction = 'out' and t.status = any(%s)"
        "   and coalesce(t.sent_at, t.created_at) < %s",
        (program_id, list(SENT_STATUSES), end))
    touches = int(cur.fetchone()["n"])

    cur.execute(
        "select d.decision, count(*) as n from action a"
        " join policy_decision d on d.id = a.policy_decision_id"
        " where a.program_id = %s and d.decided_at < %s group by d.decision",
        (program_id, end))
    decisions = {r["decision"]: int(r["n"]) for r in cur.fetchall()}

    cur.execute(
        "select kind, sum(billed_credits) as credits from cost_event"
        " where program_id = %s and occurred_at < %s group by kind", (program_id, end))
    credits = {r["kind"]: float(r["credits"] or 0) for r in cur.fetchall()}

    cur.execute(
        "select avg(amount_micros)::bigint as average, count(*) as n from opportunity"
        " where amount_micros is not null and amount_micros > 0")
    deals = cur.fetchone()
    with_amount = int(deals["n"] or 0)
    average = int(deals["average"]) if with_amount else None

    # The opportunity arms, inside the same window: a deal a year later is
    # not this period's increment, however real it is.
    cur.execute(
        "select e.variant, count(distinct o.enrollment_id) as n"
        " from enrollment e join outcome o on o.enrollment_id = e.id"
        " where e.program_id = %s and e.entered_at < %s and o.occurred_at < %s"
        "   and o.type = 'opp_created'"
        "   and o.occurred_at < e.entered_at + make_interval(days => %s)"
        " group by e.variant",
        (program_id, end, end, metric.window_days))
    opps = {r["variant"]: int(r["n"]) for r in cur.fetchall()}
    # What the tenant declared they spent on go-to-market this period, if
    # anybody did. Preferred over the baseline's prorated run-rate, which is
    # what the figure rested on when nothing re-measured it (decision 46).
    declared = period_spend_repo.get(cur, str(period["id"]))

    frozen = baseline_repo.get(cur)
    quote = None if frozen is None else BaselineQuote(
        digest=frozen["digest"], window_start=frozen["window_start"],
        window_end=frozen["window_end"],
        monthly_spend_micros=sum(int(frozen[f]) for f in (
            "spend_tools_micros", "spend_data_micros", "spend_sending_micros",
            "spend_people_micros")),
        meetings=int(frozen["meetings"]), opportunities=int(frozen["opportunities"]),
        cost_per_meeting_micros=frozen["cost_per_meeting_micros"],
        cost_per_opportunity_micros=frozen["cost_per_opportunity_micros"])

    return IncrementalityReport(
        program_key=program["key"], program_version=str(program["version"]),
        spec_hash=program["spec_hash"], primary_metric=metric.name,
        metric_window_days=metric.window_days,
        period_start=_utc_date(start), period_end=_utc_date(end),
        holdout_pct=holdout,
        primary=Comparison(
            treatment_enrolled=enrolled.get("treatment", 0),
            control_enrolled=enrolled.get("control", 0),
            treatment_converted=converted.get("treatment", 0),
            control_converted=converted.get("control", 0)),
        opportunities=Comparison(
            treatment_enrolled=enrolled.get("treatment", 0),
            control_enrolled=enrolled.get("control", 0),
            treatment_converted=opps.get("treatment", 0),
            control_converted=opps.get("control", 0)),
        converted_by_type=by_type, unread_conversions=unread, touches_sent=touches,
        decisions=decisions, credits_by_kind=credits,
        average_opportunity_micros=average, opportunities_with_amount=with_amount,
        max_cost_per_meeting_micros=ceiling_micros(program.get("spec") or {}),
        period_spend_micros=None if declared is None else int(declared["total_micros"]),
        baseline=quote)


def freeze(cur, tenant_id: str, program: dict[str, Any], period: dict[str, Any], *,
           frozen_by: str) -> dict[str, Any]:
    """Compose the report for a closed period and write it once.

    A period still open is refused: its costs are still being written, and a
    report frozen mid-period is the "stopping the test on a favourable read"
    that `docs/10` lists as an anti-pattern.
    """
    if period.get("closed_at") is None:
        raise ReportError("a report is frozen at the close of a period, and this one is open")
    report = compose(cur, program, period)
    row = reports.insert(cur, tenant_id, program_id=str(program["id"]),
                         billing_period_id=str(period["id"]), report=report,
                         frozen_by=frozen_by)
    if row is None:
        raise reports.ReportAlreadyFrozen(
            f"{program['key']} already has a report for this period; a report is frozen "
            "once and never replaced, because the number a partner read is the number "
            "the row has to hold")
    ledger.audit(cur, tenant_id, actor=frozen_by, action="report.frozen",
                 subject=str(program["id"]),
                 detail={"digest": row["digest"], "verdict": row["verdict"],
                         "period": str(period["id"])})
    return row


def freeze_all(cur, tenant: dict[str, Any], period: dict[str, Any], *,
               frozen_by: str) -> list[dict[str, Any]]:
    """One report per program that enrolled anybody before the period's end.

    Idempotent alongside `metering.close_period`: a program whose report for
    this period exists keeps it, and the existing row is what is returned.
    """
    cur.execute(
        "select distinct p.* from program p join enrollment e on e.program_id = p.id"
        " where e.entered_at < %s order by p.key, p.version", (period["ends_at"],))
    programs = [dict(r) for r in cur.fetchall()]
    existing = {str(r["program_id"]): r for r in reports.for_period(cur, str(period["id"]))}
    frozen: list[dict[str, Any]] = []
    for program in programs:
        row = existing.get(str(program["id"]))
        if row is None:
            row = freeze(cur, str(tenant["id"]), program, period, frozen_by=frozen_by)
        frozen.append(row)
    return frozen


def _utc_date(moment: datetime):
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).date()
