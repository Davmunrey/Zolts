"""The reply rates `zolts.replyrates` defines, counted per step from the
touches and outcomes the runtime already holds.

**Sent** is an outbound touch in a sent status, by the step that sent it.
**Replied** is the same touch after a provider reported a reply: the inbound
handler flips its status, so the reply is on the step it answered.
**Positive** is an outcome of type `reply_positive` on the enrolment, credited
to the last step sent to that enrolment before the reply arrived — the
runtime records the outcome on the enrolment, not on a step, and the last
send before a reply is the one the reply answered. Every query runs inside
the caller's tenant transaction.
"""

from __future__ import annotations

from typing import Any

from runtime.reporting import SENT_STATUSES
from zolts.replyrates import rows as build_rows


def for_program(cur, program_id: str, spec: dict[str, Any]) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], dict[str, int]] = {}

    def bucket(step: str, variant: str) -> dict[str, int]:
        return counts.setdefault((step, variant), {"sent": 0, "replied": 0, "positive": 0})

    cur.execute(
        "select t.step_key, e.variant, count(*) as sent,"
        "       count(*) filter (where t.status = 'replied') as replied"
        "  from touch t join enrollment e on e.id = t.enrollment_id"
        " where e.program_id = %s and t.direction = 'out' and t.step_key is not null"
        "   and t.status = any(%s)"
        " group by t.step_key, e.variant",
        (program_id, list(SENT_STATUSES)))
    for r in cur.fetchall():
        b = bucket(r["step_key"], r["variant"])
        b["sent"] = int(r["sent"])
        b["replied"] = int(r["replied"])

    # Credited to the last step sent before the reply. `lateral` rather than a
    # join on status: a reply the provider reported against no touch still
    # answered whatever went out last.
    cur.execute(
        "select last.step_key, e.variant, count(distinct o.id) as positive"
        "  from outcome o join enrollment e on e.id = o.enrollment_id"
        "  join lateral ("
        "    select t.step_key from touch t"
        "     where t.enrollment_id = e.id and t.direction = 'out'"
        "       and t.step_key is not null and t.sent_at is not null"
        "       and t.sent_at <= o.occurred_at"
        "     order by t.sent_at desc limit 1) last on true"
        " where e.program_id = %s and o.type = 'reply_positive'"
        " group by last.step_key, e.variant",
        (program_id,))
    for r in cur.fetchall():
        bucket(r["step_key"], r["variant"])["positive"] = int(r["positive"])

    return [row.as_dict() for row in build_rows(spec, counts)]
