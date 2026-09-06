"""The append-only records: policy decisions, touches, costs, outcomes, audit.

Nothing here is ever updated in place except a touch's delivery status, which
is a provider callback and not a rewrite of what was decided.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from runtime.db import one, rows


def record_decision(cur, tenant_id: str, *, subject_type: str, subject_id: str,
                    action: str, decision: str, rule_key: str,
                    jurisdiction: str | None, rationale: str | None) -> str:
    cur.execute(
        "insert into policy_decision (tenant_id, subject_type, subject_id, action,"
        " decision, rule_key, jurisdiction, rationale) values (%s,%s,%s,%s,%s,%s,%s,%s)"
        " returning id",
        (tenant_id, subject_type, subject_id, action, decision, rule_key,
         jurisdiction, rationale),
    )
    return str(one(cur)["id"])


def record_touch(cur, tenant_id: str, *, enrollment_id: str | None, channel: str,
                 step_key: str | None, idempotency_key: str, content: dict[str, Any],
                 provider: str | None, provider_ref: str | None, status: str,
                 cost_micros: int = 0, sent_at: datetime | None = None) -> dict[str, Any] | None:
    cur.execute(
        "insert into touch (tenant_id, enrollment_id, channel, step_key, idempotency_key,"
        " content, provider, provider_ref, status, cost_micros, sent_at)"
        " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " on conflict (tenant_id, idempotency_key) do update set"
        "   status = excluded.status, provider_ref ="
        "   coalesce(excluded.provider_ref, touch.provider_ref), sent_at ="
        "   coalesce(excluded.sent_at, touch.sent_at)"
        " returning *",
        (tenant_id, enrollment_id, channel, step_key, idempotency_key,
         json.dumps(content), provider, provider_ref, status, cost_micros, sent_at),
    )
    return one(cur)


def record_cost(cur, tenant_id: str, *, program_id: str | None, kind: str,
                provider: str | None, units: float, cost_micros: int,
                billed_credits: float = 0,
                billing_period_id: str | None = None) -> None:
    """What an action cost, in money and in credits.

    `cost_micros` is what it cost this company; `billed_credits` is what the
    customer owes. Every row written before `runtime.metering` existed carries
    zero credits, which is accurate: nothing was billed.
    """
    cur.execute(
        "insert into cost_event (tenant_id, program_id, kind, provider, units,"
        " cost_micros, billed_credits, billing_period_id)"
        " values (%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant_id, program_id, kind, provider, units, cost_micros, billed_credits,
         billing_period_id),
    )


def record_outcome(cur, tenant_id: str, *, enrollment_id: str | None,
                   account_id: str | None, type: str, value_micros: int | None,
                   occurred_at: datetime, source: str,
                   dedupe_key: str | None = None,
                   verified_by: str | None = None) -> dict[str, Any] | None:
    """Record an outcome, and whether anybody read what established it.

    `source` is which provider sent the event. `verified_by` is a different
    question: whether the words behind it were read. A reply that arrives with
    no body is a real event from a real provider and still tells us only that
    a human responded — the measurement needs to know the difference.
    """
    cur.execute(
        "insert into outcome (tenant_id, enrollment_id, account_id, type, value_micros,"
        " occurred_at, source, dedupe_key, verified_by)"
        " values (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " on conflict (tenant_id, dedupe_key) where dedupe_key is not null do nothing"
        " returning *",
        (tenant_id, enrollment_id, account_id, type, value_micros, occurred_at,
         source, dedupe_key, verified_by),
    )
    return one(cur)


def audit(cur, tenant_id: str, *, actor: str, action: str, subject: str | None,
          detail: dict[str, Any] | None = None) -> None:
    cur.execute(
        "insert into audit_log (tenant_id, actor, action, subject, detail)"
        " values (%s,%s,%s,%s,%s)",
        (tenant_id, actor, action, subject, json.dumps(detail or {})),
    )


def touches_this_week(cur, enrollment_id: str) -> int:
    cur.execute(
        "select count(*) as n from touch where enrollment_id = %s"
        " and sent_at > now() - interval '7 days'",
        (enrollment_id,),
    )
    return int(one(cur)["n"])


def spend_micros(cur, program_id: str | None = None) -> int:
    sql = "select coalesce(sum(cost_micros),0) as t from cost_event"
    params: tuple = ()
    if program_id:
        sql += " where program_id = %s"
        params = (program_id,)
    cur.execute(sql, params)
    return int(one(cur)["t"])


def decisions(cur, limit: int = 100) -> list[dict[str, Any]]:
    cur.execute("select * from policy_decision order by decided_at desc limit %s", (limit,))
    return rows(cur)
