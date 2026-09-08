"""Frozen incrementality reports, one per program per closed period, written once.

Write-once is the property (ADR-043): a report that can be regenerated after
somebody has read it is a report that will be, and then the number a partner
signed against is a number that moved. The rendered document is stored
verbatim beside its canonical fields, so a later template never restates it.
"""

from __future__ import annotations

import json
from typing import Any

from runtime.db import one, rows
from zolts.report import IncrementalityReport


class ReportAlreadyFrozen(ValueError):
    """This program has a report for this period, and it is not replaced."""


def insert(cur, tenant_id: str, *, program_id: str, billing_period_id: str,
           report: IncrementalityReport, frozen_by: str) -> dict[str, Any] | None:
    """Store the report, or return None because one exists for the pair.

    `on conflict do nothing` rather than select-then-insert, so two closes
    racing each other cannot both succeed with one silently overwritten.
    """
    cur.execute(
        "insert into incrementality_report (tenant_id, program_id, billing_period_id,"
        " period_start, period_end, verdict, body, digest, rendered, frozen_by)"
        " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " on conflict (tenant_id, program_id, billing_period_id) do nothing returning *",
        (tenant_id, program_id, billing_period_id, report.period_start, report.period_end,
         report.verdict, json.dumps(report.canonical()), report.digest(),
         report.render_markdown(), frozen_by))
    return one(cur)


def for_program(cur, program_id: str) -> list[dict[str, Any]]:
    """Every frozen report for a program, newest period first."""
    cur.execute(
        "select * from incrementality_report where program_id = %s"
        " order by period_end desc, frozen_at desc", (program_id,))
    return rows(cur)


def for_period(cur, billing_period_id: str) -> list[dict[str, Any]]:
    cur.execute(
        "select * from incrementality_report where billing_period_id = %s"
        " order by period_end desc, frozen_at desc", (billing_period_id,))
    return rows(cur)


def get(cur, report_id: str) -> dict[str, Any] | None:
    cur.execute("select * from incrementality_report where id = %s", (report_id,))
    return one(cur)


def as_dict(row: dict[str, Any]) -> dict[str, Any]:
    """The row as the API and the CLI report it. Never the tenant id."""
    return {
        "id": str(row["id"]),
        "program_id": str(row["program_id"]),
        "billing_period_id": str(row["billing_period_id"]),
        "period_start": row["period_start"].isoformat(),
        "period_end": row["period_end"].isoformat(),
        "verdict": row["verdict"],
        "digest": row["digest"],
        "body": row["body"],
        "rendered": row["rendered"],
        "frozen_by": row["frozen_by"],
        "frozen_at": row["frozen_at"].isoformat(),
    }
