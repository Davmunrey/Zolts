"""The funnel `zolts.signalfunnel` defines, counted from rows the runtime holds.

Four tables, one question: what did each signal produce? `signal` says what
fired. A live programme's trigger says what is listened to — the same list
`enroll.ingest` walks. `enrollment.context.signal_id` says which signal made
each enrolment, and its programme's declared metric says which outcomes count
and for how long. `touch` says who was reached. `outcome` says who converted.
Every query runs inside the caller's tenant transaction.

**The window is the programme's, not a default.** Each enrolment is judged by
the metric and the window the programme it belongs to declared, resolved
exactly as the frozen report resolves them (`runtime.reporting`), and applied
by the one predicate `zolts.signalfunnel.in_window` holds. A funnel that
counted every conversion at ninety days would agree with the report for the
programmes that declare ninety and disagree, quietly, for the ones that
declare thirty.

**Reached is the treatment arm, by construction.** A control enrolment is
never scheduled, so a touch on one would be a defect elsewhere; the query
does not count it, and the rule refuses a row where it would have.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from runtime.engine import triggers
from runtime.reporting import SENT_STATUSES
from zolts import metrics, signals
from zolts.signalfunnel import STAGES, in_window, rows as build_rows


def _metric_of(spec: dict[str, Any] | None) -> metrics.Metric:
    """The programme's declared metric, or the default it falls back to —
    the same resolution the frozen report makes, so the two cannot disagree
    about what a conversion is."""
    experiment = (spec or {}).get("experiment") or {}
    try:
        return metrics.resolve(experiment.get("primary_metric"))
    except metrics.MetricError:
        return metrics.DEFAULT


def _empty() -> dict[str, Any]:
    return {"fired": 0, "listened": 0, "enrolled": 0, "held_out": 0,
            "reached": 0, "converted": 0, "windows": set()}


def for_tenant(cur, *, catalogue: dict[str, Any] | None = None,
               now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    catalogue = signals.catalogue() if catalogue is None else catalogue
    counts: dict[str, dict[str, Any]] = {}

    def bucket(key: str) -> dict[str, Any]:
        return counts.setdefault(key, _empty())

    cur.execute("select type, count(*) as n from signal group by type")
    for r in cur.fetchall():
        bucket(r["type"])["fired"] = int(r["n"])

    # Every programme's metric, whatever its status: an enrolment made while
    # a programme was live is judged by that programme's window after it is
    # paused. Only the live ones listen, because only the live ones are what
    # `enroll.ingest` walks.
    cur.execute("select id, status, spec from program")
    programs = {str(r["id"]): (r["status"], r["spec"] or {}) for r in cur.fetchall()}
    for status, spec in programs.values():
        if status != "live":
            continue
        for key in set(triggers.signal_types(spec)):
            bucket(key)["listened"] += 1

    # The enrolments a signal made, joined on the id ingest wrote into the
    # context. Compared as text rather than cast: a context somebody wrote by
    # hand must not take the whole screen down with a bad uuid.
    cur.execute(
        "select e.id, e.program_id, e.variant, e.entered_at, s.type"
        "  from enrollment e"
        "  join signal s on s.id::text = e.context->>'signal_id'")
    enrolments: dict[str, dict[str, Any]] = {}
    for r in cur.fetchall():
        metric = _metric_of(programs.get(str(r["program_id"]), (None, {}))[1])
        enrolments[str(r["id"])] = {
            "type": r["type"], "variant": r["variant"],
            "entered_at": r["entered_at"], "metric": metric,
        }
        b = bucket(r["type"])
        b["enrolled"] += 1
        b["windows"].add(metric.window_days)
        if r["variant"] == "control":
            b["held_out"] += 1

    if enrolments:
        ids = list(enrolments)
        cur.execute(
            "select distinct t.enrollment_id::text as id"
            "  from touch t join enrollment e on e.id = t.enrollment_id"
            " where t.enrollment_id::text = any(%s) and e.variant = 'treatment'"
            "   and t.direction = 'out' and t.status = any(%s)",
            (ids, list(SENT_STATUSES)))
        for r in cur.fetchall():
            bucket(enrolments[r["id"]]["type"])["reached"] += 1

        cur.execute(
            "select o.enrollment_id::text as id, o.type, o.occurred_at"
            "  from outcome o where o.enrollment_id::text = any(%s)", (ids,))
        converted: set[str] = set()
        for r in cur.fetchall():
            e = enrolments[r["id"]]
            if r["type"] not in e["metric"].events:
                continue
            if not in_window(e["entered_at"], r["occurred_at"], e["metric"].window_days):
                continue
            converted.add(r["id"])
        for enrolment_id in converted:
            bucket(enrolments[enrolment_id]["type"])["converted"] += 1

    return {
        "kind": "signal_funnel",
        "asOf": now.isoformat(),
        "stages": [{"key": s.key, "label": s.label, "counts": s.counts} for s in STAGES],
        "rows": [row.as_dict() for row in build_rows(catalogue, counts)],
    }
