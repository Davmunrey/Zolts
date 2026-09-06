"""What a channel costs and what limits it, which is not the connector's job.

Adding a second channel is not adding a connector — that contract has existed
since the first one. Three things the worker did were email's rather than every
channel's, and each is a defect with a customer on the other end of it:

  * every dispatch was priced at `email.send`, so a HubSpot task was billed as
    an email nobody sent;
  * every dispatch allocated a mailbox seat, so that task stopped once the
    mailboxes hit their daily cap;
  * `linkedin` and `ads` were dispatchable with no provider, so the shipped
    flagship program's LinkedIn steps queued, failed, and were cancelled one at
    a time while the sequence carried on.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from runtime import channels
from runtime.engine import planner
from runtime.engine.worker import Worker
from runtime.provision import store_connection
from runtime.repo import entities
from tests.conftest import SECRET, requires_db
from tests.test_runtime_engine import DISPATCH_SPEC, _ingest, _publish

NOW = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


def _account_with_contact(cur, tid: str, *, channels_allowed=("email",)):
    """An account and a contact whose consent covers the channels under test.

    The policy pack defaults a channel it does not list to requiring consent,
    which is the fail-closed direction and not this block's to change: whether
    an internal action needs its own basis is a compliance decision, and it is
    registered rather than assumed away here.
    """
    account = entities.upsert_account(cur, tid, name="Acme",
                                      domain=f"{uuid.uuid4().hex[:8]}.com", country="ES")
    person = entities.upsert_person(
        cur, tid, email=f"{uuid.uuid4().hex[:8]}@example.com", country="ES",
        full_name="Dana Cruz",
        consent_state={c: {"basis": "consent", "source": "test"}
                       for c in channels_allowed})
    entities.link(cur, tid, str(person["id"]), str(account["id"]), buying_role="economic")
    return account, person


def _credits(cur, kind: str) -> Decimal:
    cur.execute("select coalesce(sum(billed_credits), 0) as c from cost_event"
                " where kind = %s", (kind,))
    return Decimal(str(cur.fetchone()["c"]))


# -- the definitions ------------------------------------------------------

def test_a_channel_this_runtime_cannot_execute_is_refused_by_name():
    """Fail closed, and say what is missing. A channel needs a price and a
    capacity model before it can send on its own."""
    with pytest.raises(channels.ChannelNotDefined, match="linkedin"):
        channels.channel_for("linkedin")


def test_the_planner_and_the_price_read_one_list():
    """Two literals drift, and the one that drifts is the one that spends."""
    assert planner.DISPATCHABLE == channels.dispatchable()
    assert "email" in planner.DISPATCHABLE
    assert "linkedin" not in planner.DISPATCHABLE


def test_a_channel_the_price_list_does_not_carry_is_not_priced_at_zero():
    """None is a price nobody has been asked for. Zero is one somebody chose,
    and a channel silently worth nothing is how a category becomes free."""
    assert channels.channel_for("task").credit_kind is None
    assert channels.channel_for("email").credit_kind == "email.send"


def test_only_sending_is_governed_by_the_mailbox_fleet():
    """A warmed mailbox has a daily cap and a reputation to lose. A row written
    into somebody's CRM has neither."""
    assert channels.channel_for("email").uses_mailbox_fleet
    assert not channels.channel_for("task").uses_mailbox_fleet
    assert not channels.channel_for("crm").uses_mailbox_fleet


# -- a step on an unbuilt channel reaches a person ------------------------

@requires_db
def test_a_step_on_an_unbuilt_channel_becomes_somebody_s_work(db, tenant, fake):
    """It used to queue as a dispatch, fail as a permanent error and be
    cancelled — loud in a row nobody reads, which is silent. The flagship
    shipped program has four such steps."""
    spec = {**DISPATCH_SPEC, "plays": {
        "t2": {"auto_send": True,
               "steps": [{"step": "linkedin_connect", "channel": "linkedin"}]}}}
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=spec, key=f"unbuilt-{uuid.uuid4().hex[:6]}")
        account, _ = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=70)

    tick = Worker(db, secret_key=SECRET, batch=10).tick([tid])
    assert tick.succeeded == 1 and tick.cancelled == 0, tick.errors

    with db.tenant_tx(tid) as cur:
        cur.execute("select kind from action")
        assert cur.fetchone()["kind"] == "manual"
        cur.execute("select status, content from touch")
        touch = cur.fetchone()
    assert touch["status"] == "queued"
    assert touch["content"]["awaiting"] == "human_review"


# -- what each channel costs ----------------------------------------------

@requires_db
def test_a_crm_task_is_not_billed_as_an_email(db, tenant, fake):
    """It was. `docs/12` prices no such action and the step that created it is
    already charged, so the invoice carried a send that never happened."""
    spec = {**DISPATCH_SPEC, "plays": {
        "t2": {"auto_send": True, "steps": [{"step": "log_task", "channel": "task"}]}}}
    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=spec, key=f"task-{uuid.uuid4().hex[:6]}")
        account, _ = _account_with_contact(cur, tid, channels_allowed=("email", "task"))
        _ingest(cur, tid, account["id"], score=70)

    tick = Worker(db, secret_key=SECRET, batch=10).tick([tid])
    assert tick.succeeded == 1, tick.errors

    with db.tenant_tx(tid) as cur:
        assert _credits(cur, "email.send") == Decimal("0")
        # The orchestration is still charged: the runtime did the work.
        assert _credits(cur, "program.step") == Decimal("0.2")


@requires_db
def test_an_email_step_is_still_billed_and_still_takes_a_mailbox(db, tenant, fake):
    """The seam must not have quietly freed the one channel that pays."""
    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=DISPATCH_SPEC)
        account, _ = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=70)

    tick = Worker(db, secret_key=SECRET, batch=10).tick([tid])
    assert tick.succeeded == 1, tick.errors
    with db.tenant_tx(tid) as cur:
        assert _credits(cur, "email.send") == Decimal("1")


@requires_db
def test_a_task_is_not_held_because_the_mailboxes_are_full(db, tenant, fake):
    """A limit borrowed from a channel it is not on. The fleet was consulted for
    every dispatch, so a CRM task stopped at the daily sending cap."""
    from runtime import fleet

    spec = {**DISPATCH_SPEC, "plays": {
        "t2": {"auto_send": True, "steps": [{"step": "log_task", "channel": "task"}]}}}
    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        # A registered domain with a mailbox at zero remaining capacity: the
        # fleet is managed and has nothing to give.
        # A registered domain, and a mailbox paused: the fleet is managed and
        # has nothing to give.
        cur.execute(
            "insert into sending_domain (tenant_id, name, spf, dkim, dmarc_policy,"
            " one_click_unsubscribe, paused) values"
            " (zolts_internal.current_tenant(), 'outbound.example', true, true,"
            " 'quarantine', true, false)")
        cur.execute(
            "insert into mailbox (tenant_id, address, domain, provider,"
            " warmup_started_on, paused) values"
            " (zolts_internal.current_tenant(), 'ae@outbound.example',"
            " 'outbound.example', 'google', current_date - 60, true)")
        _publish(cur, tid, spec=spec, key=f"capped-{uuid.uuid4().hex[:6]}")
        account, _ = _account_with_contact(cur, tid, channels_allowed=("email", "task"))
        _ingest(cur, tid, account["id"], score=70)
        assert not fleet.allocate(cur, recipient="someone@example.com").granted

    tick = Worker(db, secret_key=SECRET, batch=10).tick([tid])
    assert tick.deferred == 0, "a task was held by a sending cap it is not under"
    assert tick.succeeded == 1, tick.errors
