"""The frozen baseline, one per tenant, written once.

Write-once is the whole point (ADR-042): a baseline that can be corrected
after the pilot has run is a baseline that will be, and the comparison it
exists for is then a comparison with a number somebody chose afterwards.
"""

from __future__ import annotations

from typing import Any

from runtime.db import one
from zolts.baseline import Baseline


class BaselineAlreadyFrozen(ValueError):
    """The tenant has a baseline, and it is not replaced."""


def freeze(cur, tenant_id: str, baseline: Baseline, *, signed_by: str,
           captured_by: str) -> dict[str, Any]:
    """Store the baseline, or refuse because one exists.

    `on conflict do nothing` rather than a select-then-insert, so two
    concurrent captures cannot both succeed and one of them silently lose.
    """
    cur.execute(
        "insert into tenant_baseline (tenant_id, window_start, window_end,"
        " spend_tools_micros, spend_data_micros, spend_sending_micros,"
        " spend_people_micros, contacted, replied, meetings, opportunities,"
        " cost_per_meeting_micros, cost_per_opportunity_micros, source, digest,"
        " signed_by, captured_by)"
        " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " on conflict (tenant_id) do nothing returning *",
        (tenant_id, baseline.window_start, baseline.window_end,
         baseline.spend_tools_micros, baseline.spend_data_micros,
         baseline.spend_sending_micros, baseline.spend_people_micros,
         baseline.contacted, baseline.replied, baseline.meetings,
         baseline.opportunities, baseline.cost_per_meeting_micros,
         baseline.cost_per_opportunity_micros, baseline.source, baseline.digest(),
         signed_by, captured_by))
    row = one(cur)
    if row is None:
        raise BaselineAlreadyFrozen(
            "this tenant's baseline is frozen; a baseline is captured once and never "
            "replaced, because the comparison it exists for needs a number nobody "
            "chose afterwards")
    return row


def get(cur) -> dict[str, Any] | None:
    """The current tenant's baseline, or None. Tenant-scoped by the transaction."""
    cur.execute("select * from tenant_baseline limit 1")
    return one(cur)


def as_dict(row: dict[str, Any]) -> dict[str, Any]:
    """The row as the API and the CLI report it."""
    out = {k: v for k, v in row.items() if k != "tenant_id"}
    for name in ("window_start", "window_end", "frozen_at"):
        if out.get(name) is not None:
            out[name] = out[name].isoformat()
    return out
