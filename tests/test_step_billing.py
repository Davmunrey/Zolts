"""What a program step costs, and the four ways it costs nothing.

`docs/12` prices program execution at 0.2 credits and the runtime billed none
of it. It is the highest-volume action in the price list — a six-step play over
ten thousand accounts is twelve thousand of them — so the base unit of the
product's consumption was free.

The interesting assertions here are the ones about *not* charging. A step is
billed when the runtime disposed it; a step the policy gate refused, one
deferred because the tenant is out of credits or the mailbox is at its cap, and
one belonging to the holdout are all steps the customer does not owe for.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from runtime.engine.worker import Worker
from runtime.provision import store_connection
from runtime.repo import actions, enrollments, entities
from tests.conftest import SECRET
from tests.test_runtime_engine import (DISPATCH_SPEC, SPEC, _account_with_contact,
                                       _ingest, _publish)

NOW = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)

# The t1 play has `auto_send: False` and one task step, so it reaches the
# manual branch rather than a connector.
MANUAL_SPEC = {**SPEC, "experiment": {"holdout_pct": 0, "unit": "account",
                                      "salt": "manual",
                                      "holdout_waiver_reason": "deterministic fixture"}}


def _credits(cur, kind: str) -> Decimal:
    cur.execute("select coalesce(sum(billed_credits), 0) as c from cost_event"
                " where kind = %s", (kind,))
    return Decimal(str(cur.fetchone()["c"]))


def _run(db, tid: str) -> object:
    return Worker(db, secret_key=SECRET, batch=10).tick([tid])


# -- the step is charged -------------------------------------------------

def test_a_sent_step_costs_the_step_and_the_send(db, tenant, fake):
    """Two prices, deliberately. The step is orchestration; the send is the
    external action, and `docs/12` lists them separately."""
    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=DISPATCH_SPEC)
        account, _ = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=70)

    tick = _run(db, tid)
    assert tick.succeeded == 1, tick.errors

    with db.tenant_tx(tid) as cur:
        assert _credits(cur, "program.step") == Decimal("0.2")
        assert _credits(cur, "email.send") == Decimal("1")


def test_a_manual_step_is_charged_because_the_runtime_did_the_work(db, tenant, fake):
    """The runtime's job on a manual step was to create the task, and it did.
    Not charging for it would price the human-reviewed configuration at zero
    and the unattended one at full rate."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=MANUAL_SPEC, key="manual-program")
        account, _ = _account_with_contact(cur, tid)
        results = _ingest(cur, tid, account["id"])
    assert results and results[0].tier == "t1"

    tick = _run(db, tid)
    assert tick.succeeded == 1, tick.errors
    with db.tenant_tx(tid) as cur:
        assert _credits(cur, "program.step") == Decimal("0.2")
        # No connector was asked to do anything, so nothing else is charged.
        assert _credits(cur, "email.send") == Decimal("0")


def test_each_step_of_a_sequence_is_charged_once(db, tenant, fake):
    """The planner queues one step at a time, so a two-step play bills twice
    and not once — and not three times when a tick runs with nothing due."""
    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=DISPATCH_SPEC)
        account, _ = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=70)

    _run(db, tid)
    with db.tenant_tx(tid) as cur:
        # The second step waits four days. Bring the enrollment forward rather
        # than sleeping: the assertion is about counting, not about the clock.
        cur.execute("update enrollment set next_run_at = now() - interval '1 minute'"
                    " where state = 'running'")
    _run(db, tid)  # queues step two, four days out
    with db.tenant_tx(tid) as cur:
        cur.execute("update action set run_after = now() - interval '1 minute'"
                    " where state = 'pending'")
    _run(db, tid)
    _run(db, tid)  # nothing due; must not charge for looking

    with db.tenant_tx(tid) as cur:
        assert _credits(cur, "program.step") == Decimal("0.4")


# -- the step is not charged ---------------------------------------------

def test_a_step_the_policy_gate_refused_is_not_charged(db, tenant, fake):
    """Charging for a blocked step would make the safest configuration the most
    expensive one to run, which is the incentive `docs/11` exists to prevent."""
    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=DISPATCH_SPEC)
        account, person = _account_with_contact(cur, tid)
        entities.suppress(cur, tid, "email", str(person["email"]), "unsubscribed", "test")
        _ingest(cur, tid, account["id"], score=70)

    tick = _run(db, tid)
    assert tick.cancelled == 1, tick.errors
    with db.tenant_tx(tid) as cur:
        assert _credits(cur, "program.step") == Decimal("0")
        cur.execute("select billed_at from action")
        assert cur.fetchone()["billed_at"] is None


def test_a_step_deferred_for_budget_is_not_charged(db, tenant, fake):
    """A tenant who has run out has not done anything wrong. The work is held,
    and holding is not a service anybody bills for."""
    from runtime import metering

    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=DISPATCH_SPEC)
        account, _ = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=70)
        cur.execute("select * from tenant where id = %s", (tid,))
        metering.meter(cur, cur.fetchone(), kind="email.send", units=15_000)

    tick = _run(db, tid)
    assert tick.deferred == 1, tick.errors
    with db.tenant_tx(tid) as cur:
        assert _credits(cur, "program.step") == Decimal("0")


def test_the_holdout_is_never_charged(db, tenant, fake):
    """Product invariant 4 as a billing property: an untouched account is a
    free account. A holdout that costs credits is a holdout customers waive."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        program = _publish(cur, tid, key=f"holdout-{uuid.uuid4().hex[:6]}")
        account, _ = _account_with_contact(cur, tid)
        enrollments.enroll(
            cur, tid, program_id=str(program["id"]), entity_type="account",
            entity_id=str(account["id"]), variant="control", score=90, tier="t2",
            state="control", next_run_at=NOW)

    _run(db, tid)
    with db.tenant_tx(tid) as cur:
        assert actions.pending_count(cur) == 0
        assert _credits(cur, "program.step") == Decimal("0")


def test_a_step_is_charged_once_however_often_it_is_settled(db, tenant, fake):
    """At-least-once execution must not mean at-least-once billing. The mark
    lives on the action, so a second settlement finds it already billed."""
    from runtime import metering

    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=DISPATCH_SPEC)
        account, _ = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=70)
    _run(db, tid)

    with db.tenant_tx(tid) as cur:
        cur.execute("select id from action where state = 'succeeded'")
        action_id = str(cur.fetchone()["id"])
        cur.execute("select * from tenant where id = %s", (tid,))
        row = dict(cur.fetchone())
        again = metering.meter_step(cur, row, action_id)
        third = metering.meter_step(cur, row, action_id)
        assert again == Decimal("0") and third == Decimal("0")
        assert _credits(cur, "program.step") == Decimal("0.2")


# -- what the operator sees ----------------------------------------------

def test_the_spend_view_shows_what_the_steps_cost(db, tenant, fake):
    """The console groups by kind, so the moment steps are billed they appear
    beside the sends they orchestrated rather than in a number nobody split."""
    from runtime.api import console

    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=DISPATCH_SPEC)
        account, _ = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=70)
    _run(db, tid)

    with db.tenant_tx(tid) as cur:
        cur.execute("select * from tenant where id = %s", (tid,))
        view = console.spend_view(cur, dict(cur.fetchone()))
    kinds = {row["kind"]: row["credits"] for row in view["byKind"]}
    assert kinds["program.step"] == 0.2
    assert kinds["email.send"] == 1.0
