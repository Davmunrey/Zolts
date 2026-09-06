"""Sending capacity, as the runtime actually enforces it.

`zolts/deliverability.py` has modelled warm-up curves, reputation factors and
the docs/09 thresholds since the reference core existed, and its own test suite
proved the arithmetic. Nothing in the runtime imported it. The `mailbox` table
shipped in migration 002 with columns for warm-up and four rates, and no line of
code ever wrote or read one of them.

So these tests are not about the arithmetic, which was already right. They are
about whether a send is actually stopped — the property `docs/15` names as the
mitigation for the highest-impact risk in the product, and the one that was
claimed and absent.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from runtime import breakers, fleet
from runtime.engine import inbound
from runtime.engine.worker import Worker
from runtime.provision import store_connection
from runtime.repo import actions, ledger
from tests.conftest import SECRET
from tests.test_runtime_engine import (DISPATCH_SPEC, NOW, _account_with_contact,
                                       _ingest, _publish)
from zolts.deliverability import Provider

requires_db = pytest.mark.skipif(False, reason="")  # every test here needs one


def _domain(cur, name="send.example", *, authenticated=True, paused=False):
    cur.execute(
        "insert into sending_domain (tenant_id, name, spf, dkim, dmarc_policy,"
        " one_click_unsubscribe, paused) values"
        " (zolts_internal.current_tenant(), %s,%s,%s,%s,%s,%s) returning *",
        (name, authenticated, authenticated,
         "quarantine" if authenticated else "none", authenticated, paused))
    return cur.fetchone()


def _mailbox(cur, address="ae@send.example", *, provider="other",
             started: date | None = None, paused=False):
    cur.execute(
        "insert into mailbox (tenant_id, address, domain, provider,"
        " warmup_started_on, paused) values"
        " (zolts_internal.current_tenant(), %s,%s,%s,%s,%s) returning *",
        (address, address.split("@")[1], provider,
         started or (date.today() - timedelta(days=60)), paused))
    return cur.fetchone()


def _sends(cur, mailbox_id: str, n: int, *, bounced=0, complained=0,
           unsubscribed=0, when: datetime | None = None):
    """Record n sends against a mailbox, some of which went wrong."""
    moment = when or datetime.now(timezone.utc) - timedelta(days=1)
    for i in range(n):
        status = "bounced" if i < bounced else "sent"
        cur.execute(
            "insert into touch (tenant_id, channel, idempotency_key, status,"
            " sent_at, mailbox_id, complained_at, unsubscribed_at) values"
            " (zolts_internal.current_tenant(), 'email', %s,%s,%s,%s,%s,%s)",
            (f"t-{uuid.uuid4().hex}", status, moment, mailbox_id,
             moment if i < complained else None,
             moment if i < unsubscribed else None))


# -- the fleet is derived, never stored ----------------------------------

def test_rates_come_from_the_sends_that_happened(db, tenant):
    """The four rate columns were dropped rather than populated.

    A stored rate is correct when it is written and wrong from then on, and it
    fails silently: a stale 0.0% bounce rate is indistinguishable from a
    healthy mailbox.
    """
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _domain(cur)
        box = _mailbox(cur)
        _sends(cur, str(box["id"]), 200, bounced=5, complained=1, unsubscribed=3)
        loaded = fleet.load(cur)

    metrics = loaded.domains[0].mailboxes[0].metrics
    assert metrics.sent == 200
    assert metrics.bounced == 5
    assert metrics.complained == 1
    assert metrics.unsubscribed == 3
    assert abs(metrics.bounce_rate - 0.025) < 1e-9


def test_a_send_outside_the_window_does_not_count(db, tenant):
    """Providers weigh recent behaviour. A mailbox that bounced badly three
    months ago and has been clean since is not the mailbox its lifetime
    average describes."""
    tid = str(tenant["id"])
    old = datetime.now(timezone.utc) - timedelta(days=fleet.WINDOW_DAYS + 5)
    with db.tenant_tx(tid) as cur:
        _domain(cur)
        box = _mailbox(cur)
        _sends(cur, str(box["id"]), 100, bounced=90, when=old)
        loaded = fleet.load(cur)

    assert loaded.domains[0].mailboxes[0].metrics.sent == 0


def test_warmup_is_a_date_and_not_a_counter(db, tenant):
    """`warmed_days` had to be incremented by something every day. One missed
    run and a mailbox is permanently younger than it is, sending under its real
    capacity forever."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _domain(cur)
        _mailbox(cur, started=date.today() - timedelta(days=13))
        loaded = fleet.load(cur)

    assert loaded.domains[0].mailboxes[0].warmup_day == 14


# -- fail closed ---------------------------------------------------------

def test_a_domain_nobody_registered_has_no_capacity(db, tenant):
    """Missing authentication is not a reputation problem to recover from, it
    is mail filtered on arrival."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _domain(cur, "registered.example")           # so the fleet is managed
        _mailbox(cur, "ae@stranger.example")
        loaded = fleet.load(cur)

    assert [d.name for d in loaded.domains] == []
    assert loaded.capacity == 0


def test_an_unauthenticated_domain_has_no_capacity(db, tenant):
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _domain(cur, "bare.example", authenticated=False)
        _mailbox(cur, "ae@bare.example")
        loaded = fleet.load(cur)

    domain = loaded.domains[0]
    assert set(domain.authentication_issues()) == {
        "spf.missing", "dkim.missing", "dmarc.too_weak", "list_unsubscribe.missing"}
    assert domain.capacity == 0
    assert loaded.select(Provider.OTHER) is None


def test_traffic_is_segregated_by_recipient_provider(db, tenant):
    """Reputation is scored per provider, so a mailbox that mixes them hides a
    problem at one behind health at the other."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _domain(cur)
        _mailbox(cur, "google@send.example", provider="google")
        loaded = fleet.load(cur)

    assert loaded.select(Provider.GOOGLE) is not None
    assert loaded.select(Provider.MICROSOFT) is None, "microsoft traffic used a google mailbox"


def test_a_company_address_is_other_rather_than_guessed():
    """Telling a Google Workspace domain from a self-hosted one needs an MX
    lookup, which is a network call in a path that must not make one."""
    assert fleet.provider_for("dana@gmail.com") is Provider.GOOGLE
    assert fleet.provider_for("dana@outlook.com") is Provider.MICROSOFT
    assert fleet.provider_for("dana@acme.com") is Provider.OTHER
    assert fleet.provider_for(None) is Provider.OTHER
    assert fleet.provider_for("not-an-address") is Provider.OTHER


# -- what the worker does with it ----------------------------------------

def _enrolled(db, tid):
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=DISPATCH_SPEC)
        account, person = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=70)
    return person


def test_a_send_records_the_mailbox_it_went_from(db, tenant, fake):
    """Without this there is no per-mailbox accounting at all, whatever the
    connector's docstring claimed."""
    tid = str(tenant["id"])
    person = _enrolled(db, tid)
    with db.tenant_tx(tid) as cur:
        _domain(cur)
        box = _mailbox(cur)

    assert Worker(db, secret_key=SECRET).tick([tid]).succeeded == 1

    with db.tenant_tx(tid) as cur:
        cur.execute("select mailbox_id, status from touch where status = 'sent'")
        touch = cur.fetchone()
    assert touch["mailbox_id"] == box["id"]


def test_a_fleet_at_capacity_holds_the_send_rather_than_making_it(db, tenant, fake):
    """Sending past capacity is how a domain is burned, and a domain takes
    months to replace and cannot be bought."""
    tid = str(tenant["id"])
    _enrolled(db, tid)
    with db.tenant_tx(tid) as cur:
        _domain(cur)
        box = _mailbox(cur)
        # Fully warmed, so capacity is the base cap; fill today's quota.
        _sends(cur, str(box["id"]), 40, when=datetime.now(timezone.utc))

    tick = Worker(db, secret_key=SECRET).tick([tid])
    assert tick.succeeded == 0
    assert tick.deferred == 1
    assert fake.sent == {}

    with db.tenant_tx(tid) as cur:
        cur.execute("select state, last_error, run_after from action"
                    " where kind <> 'generate'")
        held = cur.fetchone()

    assert held["state"] == "pending", "the work was discarded rather than held"
    assert "capacity" in held["last_error"]
    # Due when the daily cap resets, not when the billing period turns.
    assert held["run_after"] < datetime.now(timezone.utc) + timedelta(days=2)


def test_a_tenant_with_no_fleet_still_sends_and_says_so(db, tenant, fake):
    """The provider owns those mailboxes and this runtime cannot enforce a cap
    on mailboxes it has never been told about. Refusing to send would block a
    tenant for a reason no operator could act on — so the absence is reported
    rather than enforced."""
    tid = str(tenant["id"])
    _enrolled(db, tid)

    assert Worker(db, secret_key=SECRET).tick([tid]).succeeded == 1

    with db.tenant_tx(tid) as cur:
        state = fleet.health(cur)
        seat = fleet.allocate(cur, recipient="dana@acme.com")

    assert state["managed"] is False
    assert "cannot stop a send" in state["note"]
    assert seat.granted and seat.unmanaged and seat.mailbox_id is None


# -- the cut-offs --------------------------------------------------------

def test_a_complaint_rate_past_the_cutoff_pauses_the_whole_domain(db, tenant):
    """A domain past the complaint cut-off has zero capacity including its
    healthy mailboxes. Letting a clean mailbox keep sending from a burned
    domain is not a way out; it is how the rest of the fleet follows."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _domain(cur)
        hot = _mailbox(cur, "hot@send.example")
        clean = _mailbox(cur, "clean@send.example")
        # The domain is what providers score, so the domain's own rate is what
        # the cut-off reads: 5 complaints over the 1,500 sends both mailboxes
        # made is 0.33%, past the 0.3% threshold.
        _sends(cur, str(hot["id"]), 1000, complained=5)
        _sends(cur, str(clean["id"]), 500)

        tripped = breakers.check(cur, mailbox_id=str(hot["id"]), program_id=None)
        after = fleet.health(cur)

    assert tripped.domains == ("send.example",)
    assert "complaints" in tripped.reasons[0]
    assert after["capacity"] == 0, "the clean mailbox kept sending from a burned domain"
    assert after["pausedDomains"][0]["name"] == "send.example"


def test_one_bad_mailbox_is_dropped_without_burning_its_domain(db, tenant):
    """The other half of the two-level design, and the one that would be easy
    to get wrong in the direction that costs a customer their domain.

    A mailbox over the cut-off stops sending on its own verdict. The domain
    only burns when the domain's own rate crosses, because that is the number
    the providers actually score."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _domain(cur)
        hot = _mailbox(cur, "hot@send.example")
        clean = _mailbox(cur, "clean@send.example")
        # 0.5% on this mailbox, 0.045% across the domain.
        _sends(cur, str(hot["id"]), 200, complained=1)
        _sends(cur, str(clean["id"]), 2000)

        tripped = breakers.check(cur, mailbox_id=str(hot["id"]), program_id=None)
        loaded = fleet.load(cur)
        chosen = {loaded.select(Provider.OTHER).address for _ in range(3)}

    assert tripped.domains == (), "a domain at 0.045% complaints was burned"
    assert loaded.domains[0].capacity > 0
    assert chosen == {"clean@send.example"}, "a mailbox over the cut-off was selected"


def test_a_bounce_rate_pauses_the_program_and_not_the_domain(db, tenant, fake):
    """A bounce rate is a list-quality problem: the addresses the program
    targets do not exist. Pausing the domain would stop every other program
    sharing it for a fault none of them has."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _domain(cur)
        box = _mailbox(cur)
        program = _publish(cur, tid, spec=DISPATCH_SPEC, key="bouncy")
        account, _ = _account_with_contact(cur, tid)
        from runtime.repo import enrollments
        row = enrollments.enroll(
            cur, tid, program_id=str(program["id"]), entity_type="account",
            entity_id=str(account["id"]), variant="treatment", score=70, tier="t2",
            state="active", next_run_at=NOW)
        moment = datetime.now(timezone.utc) - timedelta(hours=1)
        for i in range(100):
            cur.execute(
                "insert into touch (tenant_id, enrollment_id, channel,"
                " idempotency_key, status, sent_at, mailbox_id) values"
                " (zolts_internal.current_tenant(), %s,'email',%s,%s,%s,%s)",
                (row["id"], f"b-{i}", "bounced" if i < 5 else "sent", moment, box["id"]))

        tripped = breakers.check(cur, mailbox_id=str(box["id"]),
                                 program_id=str(program["id"]))
        cur.execute("select status from program where id = %s", (program["id"],))
        status = cur.fetchone()["status"]
        cur.execute("select paused from sending_domain")
        domain_paused = cur.fetchone()["paused"]

    assert tripped.programs == (str(program["id"]),)
    assert status == "paused"
    assert domain_paused is False, "the infrastructure was punished for the list"


def test_a_rate_below_the_sample_floor_pauses_nothing(db, tenant):
    """One bounce in three sends is 33%, and pausing on it would make every
    new mailbox unusable on its first day."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _domain(cur)
        box = _mailbox(cur)
        _sends(cur, str(box["id"]), 10, complained=5)   # 50%, on ten sends
        tripped = breakers.check(cur, mailbox_id=str(box["id"]), program_id=None)
        state = fleet.health(cur)

    assert not tripped.any
    assert state["domains"][0]["rule"] == "sample.insufficient"
    assert state["capacity"] > 0


def test_tripping_the_same_breaker_twice_is_one_incident(db, tenant):
    """A provider that redelivers a complaint must not produce a second
    pause."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _domain(cur)
        box = _mailbox(cur)
        _sends(cur, str(box["id"]), 1000, complained=5)
        first = breakers.check(cur, mailbox_id=str(box["id"]), program_id=None)
        second = breakers.check(cur, mailbox_id=str(box["id"]), program_id=None)

    assert first.domains == ("send.example",)
    assert second.domains == ()


# -- the signal the runtime could not previously tell apart ---------------

def test_a_complaint_and_an_unsubscribe_are_not_the_same_event(db, tenant):
    """Both suppress, and the suppression code was right to treat them alike.
    For reputation they are nothing alike: 0.3% complaints pauses a domain,
    2% unsubscribes triggers a review. Routed through one OPT_OUT set, the
    cut-off that matters most could never fire."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _domain(cur)
        box = _mailbox(cur)
        for kind, key in (("spam", "c1"), ("unsubscribe", "u1")):
            cur.execute(
                "insert into touch (tenant_id, channel, idempotency_key, status,"
                " sent_at, mailbox_id, provider_ref) values"
                " (zolts_internal.current_tenant(), 'email', %s,'sent',now(),%s,%s)",
                (key, box["id"], key))
            event = inbound.store(
                cur, tid, provider="smartlead", signature_ok=True,
                payload={"event_type": kind, "id": key,
                         "lead": {"email": "dana@acme.com",
                                  "custom_fields": {"zolts_idempotency_key": key}}})
            inbound.apply(cur, tid, event)

        cur.execute("select idempotency_key, complained_at, unsubscribed_at from touch"
                    " order by idempotency_key")
        rows = {r["idempotency_key"]: r for r in cur.fetchall()}

    assert rows["c1"]["complained_at"] is not None
    assert rows["c1"]["unsubscribed_at"] is None
    assert rows["u1"]["unsubscribed_at"] is not None
    assert rows["u1"]["complained_at"] is None


# -- the operator finds out before the customer does ---------------------

def test_a_paused_domain_is_a_liveness_signal(db, tenant):
    """The most expensive silent state in the product. Nothing raises, no
    request fails, the campaign simply stops — and the asset that stopped
    takes months to rebuild and cannot be bought."""
    from runtime import liveness

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _domain(cur)
        box = _mailbox(cur)
        _sends(cur, str(box["id"]), 1000, complained=5)
        breakers.check(cur, mailbox_id=str(box["id"]), program_id=None)

    signals = {s.name: s for s in liveness.check(db).signals}
    assert signals["sending domains"].ok is False
    assert "send.example" in signals["sending domains"].detail
    assert "nothing else reports" in signals["sending domains"].detail
