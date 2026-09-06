"""The engine, end to end, against a real database.

Each test asserts on a property the product claims, not on an implementation
detail: the holdout is never touched, a denied action is never retried, a
duplicate signal never produces a second enrollment, and a worker that dies
mid-flight does not send twice.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from runtime.engine import enroll, planner
from runtime.engine.worker import Worker
from runtime.provision import store_connection
from runtime.repo import actions, enrollments, entities, ledger, programs
from tests.conftest import SECRET

NOW = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)

SPEC = {
    "trigger": {
        "events": [{"signal": "funding.round",
                    "where": "payload.stage in ('series_a','series_b') and payload.amount_usd >= 5000000"}],
        "combine": "any_within",
        "window": "30d",
        "dedupe": {"key": "account_id", "cooldown": "180d"},
    },
    "score": {"floor": 55},
    "route": {"tiers": [{"key": "t1", "when": "score >= 80"},
                        {"key": "t2", "when": "score >= 55"}]},
    "plays": {
        "t2": {"auto_send": True, "steps": [
            {"step": "email_1", "channel": "email", "wait": "0d"},
            {"step": "email_2", "channel": "email", "wait": "4d"},
        ]},
        "t1": {"auto_send": False, "steps": [{"step": "task_ae", "channel": "task"}]},
    },
    "policy": {"overrides": {"max_touches_per_person_per_week": 3}},
    "experiment": {"holdout_pct": 10, "unit": "account", "salt": "test-salt"},
    "exit": [{"when": "outcome.type == 'unsubscribe'", "reason": "opted_out", "suppress": True}],
}

# Dispatch tests are about dispatch, not about assignment. A 10% holdout means
# one run in ten lands in control and the test fails for a reason that is not
# the behaviour under test, so those tests use a justified zero holdout and the
# assignment invariants are tested on their own.
DISPATCH_SPEC = {**SPEC, "experiment": {"holdout_pct": 0, "unit": "account", "salt": "dispatch",
                                        "holdout_waiver_reason": "deterministic test fixture"}}


def _publish(cur, tenant_id: str, spec=None, key="test-program") -> dict:
    row = programs.publish(cur, tenant_id, key=key, version="1.0.0",
                           spec=spec or SPEC, spec_hash="deadbeef", status="draft")
    return programs.activate(cur, str(row["id"]))


def _account_with_contact(cur, tenant_id: str, *, country="ES", basis="legitimate_interest"):
    account = entities.upsert_account(cur, tenant_id, name="Acme",
                                      domain=f"{uuid.uuid4().hex[:8]}.com", country=country)
    person = entities.upsert_person(
        cur, tenant_id, email=f"{uuid.uuid4().hex[:8]}@example.com", country=country,
        full_name="Dana Cruz",
        consent_state={"email": {"basis": basis, "source": "test"}})
    entities.link(cur, tenant_id, str(person["id"]), str(account["id"]),
                  buying_role="economic")
    return account, person


def _ingest(cur, tenant_id, account_id, *, amount=9_000_000, dedupe=None, score=None):
    """Returns the enrollments, which is what every caller here asserts on."""
    return enroll.ingest(
        cur, tenant_id, entity_type="account", entity_id=str(account_id),
        type="funding.round", strength=0.9, half_life_h=720, source="test",
        legal_basis="legitimate_interest",
        payload={"stage": "series_a", "amount_usd": amount},
        observed_at=NOW, dedupe_key=dedupe, score=score, now=NOW).enrollments


# -- enrollment ----------------------------------------------------------

def test_a_matching_signal_enrolls_the_account(db, tenant, fake):
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid)
        account, _ = _account_with_contact(cur, tid)
        results = _ingest(cur, tid, account["id"])
    assert len(results) == 1
    # strength 0.9 with no explicit score means 90, which routes to tier 1.
    assert results[0].tier == "t1"
    assert results[0].variant in {"treatment", "control"}


def test_a_signal_below_the_threshold_does_not_enroll(db, tenant, fake):
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid)
        account, _ = _account_with_contact(cur, tid)
        assert _ingest(cur, tid, account["id"], amount=1_000_000) == []


def test_a_replayed_signal_does_not_enroll_twice(db, tenant, fake):
    """A source that replays its feed must not inflate anything."""
    tid = str(tenant["id"])
    key = f"dedupe-{uuid.uuid4().hex}"
    with db.tenant_tx(tid) as cur:
        program = _publish(cur, tid)
        account, _ = _account_with_contact(cur, tid)
        first = _ingest(cur, tid, account["id"], dedupe=key)
        second = _ingest(cur, tid, account["id"], dedupe=key)
        counts = enrollments.variant_counts(cur, str(program["id"]))
    assert len(first) == 1 and second == []
    assert sum(counts.values()) == 1


def test_a_program_without_a_holdout_is_refused(db, tenant, fake):
    """Product invariant 4, enforced where it can actually be violated."""
    spec = {**SPEC}
    spec.pop("experiment")
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=spec, key="no-holdout")
        account, _ = _account_with_contact(cur, tid)
        with pytest.raises(enroll.HoldoutMissing):
            _ingest(cur, tid, account["id"])


def test_a_zero_holdout_needs_a_written_justification(db, tenant, fake):
    tid = str(tenant["id"])
    waived = {**SPEC, "experiment": {"holdout_pct": 0, "salt": "s"}}
    justified = {**SPEC, "experiment": {"holdout_pct": 0, "salt": "s",
                                        "holdout_waiver_reason": "regulated pilot, n=12"}}
    with db.tenant_tx(tid) as cur:
        with pytest.raises(enroll.HoldoutMissing):
            enroll.holdout_pct(waived, "k")
        assert enroll.holdout_pct(justified, "k") == 0


# -- the holdout is never touched ---------------------------------------

def test_a_control_enrollment_never_produces_an_action(db, tenant, fake):
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        program = _publish(cur, tid)
        # Enrol a control assignment directly: finding an entity id that hashes
        # into the 10% bucket would test the hash, not the invariant.
        account, _ = _account_with_contact(cur, tid)
        row = enrollments.enroll(
            cur, tid, program_id=str(program["id"]), entity_type="account",
            entity_id=str(account["id"]), variant="control", score=90, tier="t2",
            state="control", next_run_at=NOW)
        planned = planner.plan_next(cur, tid, row, program, now=NOW)
        assert planned is None
        assert actions.pending_count(cur) == 0


# -- planning and dispatch ----------------------------------------------

def test_the_worker_plans_then_sends_one_step(db, tenant, fake):
    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=DISPATCH_SPEC)
        account, _ = _account_with_contact(cur, tid)
        results = _ingest(cur, tid, account["id"], score=70)
    assert results and results[0].variant == "treatment"

    worker = Worker(db, secret_key=SECRET, batch=10)
    tick = worker.tick([tid])
    assert tick.planned == 1
    assert tick.claimed == 1 and tick.succeeded == 1, tick.errors
    assert len(fake.sent) == 1

    with db.tenant_tx(tid) as cur:
        cur.execute("select * from touch")
        touches = cur.fetchall()
    assert len(touches) == 1 and touches[0]["status"] == "sent"


def test_a_denied_action_is_cancelled_not_retried(db, tenant, fake):
    """Retrying a suppression would be an attempt to contact someone twice."""
    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=DISPATCH_SPEC)
        account, person = _account_with_contact(cur, tid)
        entities.suppress(cur, tid, "email", str(person["email"]), "unsubscribed", "test")
        _ingest(cur, tid, account["id"], score=70)

    tick = Worker(db, secret_key=SECRET).tick([tid])
    assert tick.cancelled == 1 and tick.succeeded == 0
    assert fake.sent == {}

    with db.tenant_tx(tid) as cur:
        assert actions.pending_count(cur) == 0
        recorded = ledger.decisions(cur)
    assert recorded and recorded[0]["decision"] == "deny"


def test_every_dispatched_action_records_a_policy_decision(db, tenant, fake):
    """Product invariant 3."""
    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=DISPATCH_SPEC)
        account, _ = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=70)
    Worker(db, secret_key=SECRET).tick([tid])
    with db.tenant_tx(tid) as cur:
        cur.execute("select a.id, a.policy_decision_id from action a where a.state='succeeded'")
        rows = cur.fetchall()
    assert rows and all(r["policy_decision_id"] is not None for r in rows)


def test_a_human_review_step_is_queued_rather_than_sent(db, tenant, fake):
    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=DISPATCH_SPEC)
        account, _ = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=95)   # routes to t1, auto_send false
    tick = Worker(db, secret_key=SECRET).tick([tid])
    assert tick.succeeded == 1
    assert fake.sent == {}, "a tier-1 step must never reach a provider unattended"
    with db.tenant_tx(tid) as cur:
        cur.execute("select status from touch")
        assert cur.fetchone()["status"] == "queued"


# -- durability ----------------------------------------------------------

def test_a_transient_failure_is_retried_with_backoff(db, tenant, fake):
    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    fake.fail_times = 1
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=DISPATCH_SPEC)
        account, _ = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=70)

    worker = Worker(db, secret_key=SECRET)
    first = worker.tick([tid])
    assert first.failed == 1 and first.succeeded == 0

    with db.tenant_tx(tid) as cur:
        cur.execute("select state, attempts, run_after > now() as deferred from action")
        row = cur.fetchone()
    assert row["state"] == "pending" and row["attempts"] == 1 and row["deferred"]


def test_an_action_is_buried_after_its_attempt_budget(db, tenant, fake):
    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    fake.fail_times = 99
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=DISPATCH_SPEC)
        account, _ = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=70)

    worker = Worker(db, secret_key=SECRET)
    for _ in range(6):
        worker.tick([tid])
        with db.tenant_tx(tid) as cur:   # skip the backoff, not the accounting
            cur.execute("update action set run_after = now() - interval '1 hour'"
                        " where state = 'pending'")
    with db.tenant_tx(tid) as cur:
        assert len(actions.dead_letter(cur)) == 1
        assert actions.pending_count(cur) == 0


def test_a_redelivered_action_does_not_send_twice(db, tenant, fake):
    """Simulates a worker dying after the provider call but before the commit."""
    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=DISPATCH_SPEC)
        account, _ = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=70)

    worker = Worker(db, secret_key=SECRET)
    worker.plan_due(tid)
    claimed = worker.claim()
    assert len(claimed) == 1
    action_id, _ = claimed[0]

    from runtime.engine.worker import Tick

    t = Tick()
    worker.execute_one(action_id, tid, t)
    # Force the row back to leased, as an expired lease reclaim would.
    with db.tenant_tx(tid) as cur:
        cur.execute("update action set state = 'leased' where id = %s", (action_id,))
    worker.execute_one(action_id, tid, t)

    assert len(fake.sent) == 1, "the idempotency key must absorb the redelivery"
    with db.tenant_tx(tid) as cur:
        cur.execute("select count(*) as n from touch")
        assert cur.fetchone()["n"] == 1


def test_a_tenant_without_a_connection_fails_loudly(db, tenant, fake):
    """Silently doing nothing while reporting success is the worst outcome."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=DISPATCH_SPEC)
        account, _ = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=70)
    tick = Worker(db, secret_key=SECRET).tick([tid])
    assert tick.cancelled == 1 and tick.succeeded == 0
    with db.tenant_tx(tid) as cur:
        cur.execute("select last_error from action")
        assert "no active connection" in cur.fetchone()["last_error"]


def test_exiting_an_enrollment_cancels_its_queued_work(db, tenant, fake):
    """An opt-out recorded today must not send tomorrow's step."""
    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        program = _publish(cur, tid, spec=DISPATCH_SPEC)
        account, _ = _account_with_contact(cur, tid)
        results = _ingest(cur, tid, account["id"], score=70)
        row = enrollments.get(cur, results[0].enrollment_id)
        planner.plan_next(cur, tid, row, program, now=NOW)
        assert actions.pending_count(cur) == 1

        row = enrollments.get(cur, results[0].enrollment_id)
        reason = planner.apply_exits(cur, tid, row, program,
                                     {"outcome": {"type": "unsubscribe"}})
        assert reason == "opted_out"
        assert actions.pending_count(cur) == 0
