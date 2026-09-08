"""Every index this schema declares, and the query it was declared for.

`scripts/unused_indexes.py` resets the statistics, runs the whole suite as a
workload and reads `pg_stat_user_indexes.idx_scan` back. Four plain indexes had
never been scanned once. Three were the same defect (D-60) — a partial index
whose predicate is the *complement* of the only query that exists — and the
fourth was an index waiting for a read nobody had written (D-61).

A scan count cannot be asserted directly: the planner correctly prefers a
sequential scan on a table holding tens of rows, so a test that demanded a scan
would be testing the fixture's size. These assert the durable thing instead —
that the index *can* serve the query — by disabling sequential scans and
reading the plan.
"""

from __future__ import annotations

import uuid

from tests.conftest import requires_db

# The sweep in `runtime/cli.py`, once for each buyable field. RLS supplies the
# tenant equality in production; the plan is only ordered when it is present,
# which is why it appears here.
SWEEPS = [
    ("person_email_missing_ix", "person", "email is null"),
    ("person_phone_missing_ix", "person", "phone is null"),
    ("account_firmographics_missing_ix", "account",
     "(employee_band is null or industry_code is null)"),
]


def _plan(cur, sql: str) -> str:
    cur.execute("set local enable_seqscan = off")
    cur.execute(f"explain {sql}")
    return "\n".join(str(r["QUERY PLAN"]) for r in cur.fetchall())


@requires_db
def test_each_enrichment_sweep_has_an_index_that_serves_it(db, tenant):
    """The sweep asks for the rows that are *missing* a field. `person_phone_ix`
    covered `where phone is not null` — the rows the query excludes — so no
    volume and no plan could ever have used it."""
    tid = str(tenant["id"])
    with db.admin_tx() as cur:
        for index, table, predicate in SWEEPS:
            sql = (f"select * from {table} where tenant_id = '{tid}'::uuid"
                   f" and {predicate} order by created_at limit 50")
            plan = _plan(cur, sql)
            assert index in plan, (
                f"the {table} sweep for `{predicate}` does not use {index}:\n{plan}")


@requires_db
def test_the_sweep_walks_the_index_in_order_rather_than_sorting(db, tenant):
    """The point of leading with `tenant_id` and following with `created_at`.

    With the tenant equality that RLS supplies, the planner walks the index in
    `created_at` order and stops at the limit. Without the ordering column in
    the index it would read every matching row and sort them, and the `limit`
    would save nothing — which is the difference between an index that helps
    and one that only appears to.
    """
    tid = str(tenant["id"])
    with db.admin_tx() as cur:
        for index, table, predicate in SWEEPS:
            sql = (f"select * from {table} where tenant_id = '{tid}'::uuid"
                   f" and {predicate} order by created_at limit 50")
            plan = _plan(cur, sql)
            assert "Sort" not in plan, (
                f"the {table} sweep sorts rather than walking {index} in order:\n{plan}")


@requires_db
def test_the_indexes_that_could_never_be_read_are_gone(db):
    """`person_phone_ix` indexed the complement of the only query.
    `action_billed_ix` indexed `billed_at is not null` while the only statement
    reading `billed_at` finds its row by primary key and filters on
    `billed_at is null`; no report groups billed actions by programme."""
    with db.admin_tx() as cur:
        cur.execute("select indexname from pg_indexes where schemaname = 'public'")
        present = {r["indexname"] for r in cur.fetchall()}
    for gone in ("person_phone_ix", "action_billed_ix"):
        assert gone not in present, f"{gone} indexes rows no query asks for"


# -- the read the fourth index was waiting for (D-61) ------------------------

def _unhandled(cur, tenant_id: str, n: int, minutes_ago: int = 30) -> None:
    for _ in range(n):
        cur.execute(
            "insert into inbound_event (tenant_id, provider, event_type, external_id,"
            " payload, signature_ok, handled, error, received_at)"
            " values (%s,'postmark','bounce',%s,'{}'::jsonb,true,false,'boom',"
            "         now() - (%s * interval '1 minute'))",
            (tenant_id, str(uuid.uuid4()), minutes_ago))


@requires_db
def test_liveness_is_quiet_when_everything_received_was_handled(db, tenant):
    from runtime import liveness

    signal = next(s for s in liveness.check(db).signals if s.name == "inbound handled")
    assert signal.ok and signal.value == 0, signal.detail


@requires_db
def test_liveness_reports_inbound_events_that_were_never_handled(db, tenant):
    """A bounce or an unsubscribe whose handler raised keeps `handled = false`
    and its error, and nothing looked at it again. Suppression and measurement
    are both fed from these events, so the quiet outcome is a suppression never
    applied and a conversion never counted."""
    from runtime import liveness

    tid = str(tenant["id"])
    with db.admin_tx() as cur:
        _unhandled(cur, tid, liveness.UNHANDLED_THRESHOLD)

    signal = next(s for s in liveness.check(db).signals if s.name == "inbound handled")
    assert not signal.ok, "a wall of unhandled inbound events is reported healthy"
    assert signal.value == liveness.UNHANDLED_THRESHOLD
    assert "never handled" in signal.detail and "error" in signal.detail


@requires_db
def test_an_event_still_in_flight_is_not_counted_against_the_deployment(db, tenant):
    """An inbound event is handled in the same request that received it, so a
    row written a second ago may belong to a request still on the stack.
    Counting it would make the signal fail under load, which is when an
    operator most needs to believe it."""
    from runtime import liveness

    tid = str(tenant["id"])
    with db.admin_tx() as cur:
        _unhandled(cur, tid, liveness.UNHANDLED_THRESHOLD * 2, minutes_ago=0)

    signal = next(s for s in liveness.check(db).signals if s.name == "inbound handled")
    assert signal.ok and signal.value == 0, (
        f"an event received a moment ago was counted as unhandled: {signal.detail}")


# -- the detector's own escape hatch ----------------------------------------

@requires_db
def test_every_accepted_index_still_exists(db):
    """`scripts/unused_indexes.py` lists indexes whose zero is understood, with
    a written reason. An entry naming an index that has since been dropped or
    renamed is worse than no entry: it reads as diligence and suppresses
    nothing, and the next real finding hides behind it. Whatever it is, it is
    not a reason, because there is nothing left for it to be a reason about."""
    from scripts.unused_indexes import ACCEPTED

    with db.admin_tx() as cur:
        cur.execute("select indexname from pg_indexes where schemaname = 'public'")
        present = {r["indexname"] for r in cur.fetchall()}

    for name, reason in ACCEPTED.items():
        assert name in present, (
            f"unused_indexes.py accepts {name!r} with the reason {reason!r}, and no "
            f"such index exists. Delete the entry or restore the index")
        assert reason.strip(), f"{name} is accepted with an empty reason"
