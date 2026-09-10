"""The frequency cap counts a person, across every programme and every channel.

`zolts.policy` has enforced `max_touches_per_person_per_week` since the policy
engine shipped. `docs/09` calls it **global** — *"if they received an email and
a LinkedIn invitation this week, the third touch is delayed even if it comes
from a different program"* — and `docs/06` promises *cross-program
deduplication at person level*.

None of that was true. The gate counted `touch where enrollment_id = ?`, and an
enrolment is on an **account** in every shipped programme, so the cap was
neither per person nor global. It was per account per programme, and it was
wrong in both directions at once: five contacts at one account shared a budget
of three inside one programme, and the same person enrolled in three programmes
got three separate budgets (D-92).

It could not have been right, because the row did not say who received it. The
recipient is resolved at dispatch and was thrown away immediately after. So the
fix is a column and a different query, and these tests are about the property
rather than the query: they build the situation the document describes and ask
what the gate says.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import requires_db

CAP = 3


def _person(cur, tid, *, country="US"):
    from runtime.repo import entities

    return entities.upsert_person(
        cur, tid, email=f"f-{uuid.uuid4().hex[:8]}@example.com", full_name="F",
        country=country,
        consent_state={"email": {"basis": "consent", "source": "test"},
                       "linkedin": {"basis": "consent", "source": "test"}})


def _touch(cur, tid, person_id, *, channel="email", enrollment_id=None,
           sent=True, direction="out"):
    from runtime.repo import ledger

    row = ledger.record_touch(
        cur, tid, enrollment_id=enrollment_id, channel=channel, step_key="s",
        idempotency_key=f"k-{uuid.uuid4().hex}", content={}, provider="fake",
        provider_ref=None, status="sent" if sent else "failed",
        person_id=person_id,
        sent_at=datetime.now(timezone.utc) if sent else None)
    if direction == "in":
        cur.execute("update touch set direction = 'in' where id = %s", (row["id"],))
    return row


def _check(cur, tid, person):
    from runtime.engine import gate

    return gate.check(cur, tid, person=dict(person), channel="email",
                      enrollment_id=None, program_spec={})


# ── the premise ───────────────────────────────────────────────────────────

@requires_db
def test_a_touch_records_who_it_reached(db, tenant):
    """The column the whole cap rests on. Without it every assertion below is
    counting zero and passing for the wrong reason."""
    from runtime.repo import ledger

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        person = _person(cur, tid)
        row = _touch(cur, tid, str(person["id"]))
        assert str(row["person_id"]) == str(person["id"])
        assert ledger.touches_this_week(cur, str(person["id"])) == 1


@requires_db
def test_the_gate_is_reading_the_cap_this_file_assumes(db, tenant):
    """`max_touches_per_person_per_week` defaults to three. The tests below
    count to it, so the number is asserted rather than assumed."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        person = _person(cur, tid)
        for _ in range(CAP):
            _touch(cur, tid, str(person["id"]))
        assert not _check(cur, tid, person).allowed


# ── what the documents promise ────────────────────────────────────────────

@requires_db
def test_three_programmes_share_one_budget(db, tenant):
    """The headline. Enrolled in three programmes, a person used to get three
    budgets of three — nine touches a week, every one of them reported
    compliant."""
    from runtime.repo import ledger

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        person = _person(cur, tid)
        for programme in range(3):
            _touch(cur, tid, str(person["id"]), enrollment_id=None)
            assert ledger.touches_this_week(cur, str(person["id"])) == programme + 1
        verdict = _check(cur, tid, person)

    assert not verdict.allowed, (
        "three touches from three programmes did not reach the cap, so the cap "
        "is still counting something other than the person")
    assert "frequency" in verdict.rule_key or "touch" in verdict.rule_key, verdict.rule_key


@requires_db
def test_the_cap_counts_across_channels(db, tenant):
    """`docs/09`: an email and a LinkedIn invitation in the same week delay the
    third touch. A per-channel budget would be three of each."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        person = _person(cur, tid)
        _touch(cur, tid, str(person["id"]), channel="email")
        _touch(cur, tid, str(person["id"]), channel="linkedin")
        _touch(cur, tid, str(person["id"]), channel="task")
        assert not _check(cur, tid, person).allowed


@requires_db
def test_two_people_at_one_account_do_not_share_a_budget(db, tenant):
    """The other direction, and the half nobody would have noticed as a
    complaint: counting by an account's enrolment held a colleague who had been
    sent nothing."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        one, two = _person(cur, tid), _person(cur, tid)
        for _ in range(CAP):
            _touch(cur, tid, str(one["id"]))
        assert not _check(cur, tid, one).allowed
        assert _check(cur, tid, two).allowed, (
            "a contact who has been sent nothing is being held by a colleague's "
            "touches")


# ── what must not consume the budget ──────────────────────────────────────

@requires_db
def test_a_blocked_touch_does_not_spend_the_budget(db, tenant):
    """A policy refusal writes a row with no `sent_at`. Counting it would let a
    contact the gate blocked consume the allowance of one it allowed."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        person = _person(cur, tid)
        for _ in range(CAP + 2):
            _touch(cur, tid, str(person["id"]), sent=False)
        assert _check(cur, tid, person).allowed


@requires_db
def test_a_reply_does_not_spend_the_budget(db, tenant):
    """An inbound touch is something the person did, not something we sent."""
    from runtime.repo import ledger

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        person = _person(cur, tid)
        for _ in range(CAP + 2):
            _touch(cur, tid, str(person["id"]), direction="in")
        assert ledger.touches_this_week(cur, str(person["id"])) == 0
        assert _check(cur, tid, person).allowed


@requires_db
def test_a_touch_outside_the_window_does_not_spend_the_budget(db, tenant):
    from runtime.repo import ledger

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        person = _person(cur, tid)
        for _ in range(CAP):
            row = _touch(cur, tid, str(person["id"]))
            cur.execute("update touch set sent_at = %s where id = %s",
                        (datetime.now(timezone.utc) - timedelta(days=8), row["id"]))
        assert ledger.touches_this_week(cur, str(person["id"])) == 0
        assert _check(cur, tid, person).allowed


@requires_db
def test_a_redelivery_does_not_blank_the_person(db, tenant):
    """The upsert path. A retry that could not resolve the contact must keep
    the one the first attempt recorded, or the cap stops counting somebody it
    had already counted."""
    from runtime.repo import ledger

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        person = _person(cur, tid)
        key = f"k-{uuid.uuid4().hex}"
        for person_id in (str(person["id"]), None):
            ledger.record_touch(
                cur, tid, enrollment_id=None, channel="email", step_key="s",
                idempotency_key=key, content={}, provider="fake", provider_ref=None,
                status="sent", person_id=person_id,
                sent_at=datetime.now(timezone.utc))
        assert ledger.touches_this_week(cur, str(person["id"])) == 1


# ── the call sites, which every test above passes without ─────────────────

@requires_db
def test_a_sent_touch_names_the_person_the_worker_actually_contacted(db, tenant, fake):
    """Through the worker, end to end.

    Every test above builds its touches with `record_touch` directly, so all of
    them pass while the dispatch path records no person at all — which is what
    a mutation found: deleting `person_id` from the send call site left the
    whole suite green. The same shape as D-85, and the same fix: prove the call
    site, not only the helper.
    """
    from runtime.engine.worker import Worker
    from runtime.provision import store_connection
    from tests.conftest import SECRET
    from tests.test_runtime_engine import (DISPATCH_SPEC, _account_with_contact,
                                           _ingest, _publish)

    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=DISPATCH_SPEC)
        account, person = _account_with_contact(cur, tid)
        results = _ingest(cur, tid, account["id"], score=70)
    assert results and results[0].variant == "treatment", "the account landed in the holdout"

    tick = Worker(db, secret_key=SECRET, batch=10).tick([tid])
    assert tick.succeeded == 1, tick.errors

    with db.tenant_tx(tid) as cur:
        cur.execute("select person_id, status from touch where status = 'sent'")
        sent = cur.fetchall()
    assert len(sent) == 1
    assert sent[0]["person_id"] is not None, (
        "the worker sent to somebody and recorded no person, so the frequency "
        "cap cannot count it")
    assert str(sent[0]["person_id"]) == str(person["id"])


def test_every_touch_the_worker_records_names_a_person():
    """The other three call sites, pinned.

    An enrolment is on an account, so a touch that does not name its person
    cannot be counted against a per-person cap — and there is no failure until
    somebody is contacted four times in a week, which nobody in this repository
    will ever observe. A missing keyword is caught here instead.
    """
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent /
              "runtime" / "engine" / "worker.py").read_text()
    calls = [node for node in ast.walk(ast.parse(source))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr == "record_touch"]
    assert len(calls) >= 4, f"the worker records touches somewhere else now: {len(calls)}"
    for call in calls:
        assert "person_id" in {kw.arg for kw in call.keywords}, (
            f"a record_touch call at line {call.lineno} names no person; the "
            f"frequency cap counts nothing for it")
