"""Enrollment lifecycle."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from runtime.db import one, rows


def enroll(cur, tenant_id: str, *, program_id: str, entity_type: str, entity_id: str,
           variant: str, score: float | None, tier: str | None, state: str,
           context: dict[str, Any] | None = None,
           next_run_at: datetime | None = None) -> dict[str, Any] | None:
    """Enroll an entity. Returns None when it is already enrolled.

    The uniqueness is the database's, not the application's: two workers racing
    on the same signal must not produce two enrollments and therefore two
    holdout assignments for one entity.
    """
    cur.execute(
        "insert into enrollment (tenant_id, program_id, entity_type, entity_id, variant,"
        " score, tier, state, context, next_run_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " on conflict (tenant_id, program_id, entity_type, entity_id) do nothing"
        " returning *",
        (tenant_id, program_id, entity_type, entity_id, variant, score, tier, state,
         json.dumps(context or {}), next_run_at),
    )
    return one(cur)


def in_cooldown(cur, program_id: str, entity_id: str, cooldown_days: int) -> bool:
    """True when this entity left the program recently enough to be off limits."""
    cur.execute(
        "select 1 from enrollment where program_id = %s and entity_id = %s"
        " and entered_at > now() - make_interval(days => %s) limit 1",
        (program_id, entity_id, cooldown_days),
    )
    return cur.fetchone() is not None


def get(cur, enrollment_id: str) -> dict[str, Any] | None:
    cur.execute("select * from enrollment where id = %s", (enrollment_id,))
    return one(cur)


def due(cur, limit: int = 100) -> list[dict[str, Any]]:
    cur.execute(
        "select * from enrollment where exited_at is null and next_run_at is not null"
        " and next_run_at <= now() order by next_run_at limit %s",
        (limit,),
    )
    return rows(cur)


def advance(cur, enrollment_id: str, *, step_index: int, state: str,
            next_run_at: datetime | None) -> None:
    cur.execute(
        "update enrollment set step_index = %s, state = %s, next_run_at = %s"
        " where id = %s",
        (step_index, state, next_run_at, enrollment_id),
    )


def exit_enrollment(cur, enrollment_id: str, reason: str) -> dict[str, Any] | None:
    cur.execute(
        "update enrollment set exited_at = now(), exit_reason = %s, next_run_at = null,"
        " state = 'exited' where id = %s and exited_at is null returning *",
        (reason, enrollment_id),
    )
    return one(cur)


def listing(cur, program_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    sql = "select * from enrollment"
    params: list[Any] = []
    if program_id:
        sql += " where program_id = %s"
        params.append(program_id)
    sql += " order by entered_at desc limit %s"
    params.append(limit)
    cur.execute(sql, params)
    return rows(cur)


def variant_counts(cur, program_id: str) -> dict[str, int]:
    cur.execute(
        "select variant, count(*) as n from enrollment where program_id = %s group by variant",
        (program_id,),
    )
    return {r["variant"]: int(r["n"]) for r in cur.fetchall()}
