"""A period's declared go-to-market spend, one row per period, written once.

Write-once for the same reason the baseline is (ADR-042): the incrementality
report divides by this figure, and a figure that can be corrected after the
result is read is one that will be. Declaring is optional — a period with no
declaration falls back to the baseline's prorated run-rate and the report says
which basis it used — because a month's close must not wait on data entry.
"""

from __future__ import annotations

from typing import Any

from runtime.db import one
from zolts.spend import PeriodSpend


class SpendAlreadyDeclared(ValueError):
    """This period has a declaration, and it is not replaced."""


class ReportAlreadyFrozen(ValueError):
    """The period's reports are frozen; a declaration now would change nothing
    and disagree with the documents."""


def declare(cur, tenant_id: str, period_id: str, spend: PeriodSpend, *,
            declared_by: str) -> dict[str, Any]:
    """Record the declaration, or refuse because one exists or it is too late.

    The frozen-report check comes first because it is the more surprising
    refusal: an operator who declares after the close has not made a duplicate
    mistake, they have missed the window, and the two need different words.
    """
    cur.execute(
        "select 1 from incrementality_report r join billing_period p"
        "  on p.starts_at::date <= r.period_start and r.period_end <= p.ends_at::date"
        " where p.id = %s limit 1", (period_id,))
    if one(cur) is not None:
        raise ReportAlreadyFrozen(
            "this period's incrementality reports are frozen, so a declaration now "
            "would change no document and disagree with every one of them. The "
            "figures a report divided by are the figures it names")

    cur.execute(
        "insert into tenant_period_spend (period_id, tenant_id, spend_tools_micros,"
        " spend_data_micros, spend_sending_micros, spend_people_micros, total_micros,"
        " digest, declared_by) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " on conflict (period_id) do nothing returning *",
        (period_id, tenant_id, spend.spend_tools_micros, spend.spend_data_micros,
         spend.spend_sending_micros, spend.spend_people_micros, spend.total_micros,
         spend.digest(), declared_by))
    row = one(cur)
    if row is None:
        raise SpendAlreadyDeclared(
            "this period's spend is declared; it is recorded once and never replaced, "
            "because a figure a signed report divides by must not be one somebody "
            "chose after reading the result")
    return row


def get(cur, period_id: str) -> dict[str, Any] | None:
    """The declaration for one period, or None. Scoped by the transaction."""
    cur.execute("select * from tenant_period_spend where period_id = %s", (period_id,))
    return one(cur)


def as_dict(row: dict[str, Any]) -> dict[str, Any]:
    """The row as the command line reports it."""
    out = {k: v for k, v in row.items() if k != "tenant_id"}
    out["period_id"] = str(out["period_id"])
    out["declared_at"] = row["declared_at"].isoformat()
    return out
