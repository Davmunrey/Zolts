"""The per-tier capacity a programme declares, which routing never counted.

`docs/04` has said since the first draft that **human capacity is a finite
resource and is modelled as one**. It was not modelled at all: the name
`capacity_per_week` appeared nowhere under `runtime/` (D-81). The flagship
programme caps t1 at 25 with the comment *the team's real human capacity*, and
t1 of a 1:1 motion is the human-reviewed tier — `zolts/dsl.py` refuses a t1
play that auto-sends. Over-admitting to it fills a review queue past what
anybody can work, so proposals age out or are approved unread, which is the
failure the propose-then-dispose invariant exists to prevent.

It hid behind a real capacity gate: `runtime/fleet.py` and `worker.py` do
enforce capacity, per mailbox and per domain (ADR-020), and an operator
watching deferrals work concludes the programme's own block is what works. The
same confusion as D-63, where the tenant credit ceiling firing made
`spec.budget` look enforced.

The sibling field is a different matter and is not fixed here. There is no
representative anywhere in the data model, so `capacity_per_week_per_rep` has
nothing to count against; it waits on decision 50 and the tests below assert
that it is still registered as unenforceable rather than quietly forgotten.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import yaml

from runtime.engine import enroll
from runtime.repo import enrollments
from tests.conftest import requires_db
from tests.test_runtime_engine import _account_with_contact, _ingest, _publish
from zolts import controls

ROOT = Path(__file__).resolve().parent.parent

TIERS = [
    {"key": "t1", "when": "score >= 80", "capacity_per_week": 2},
    {"key": "t2", "when": "score >= 65", "capacity_per_week": 3},
    {"key": "t3", "when": "score >= 55"},
]
SPEC = {
    "trigger": {"events": [{"signal": "funding.round"}], "window": "30d"},
    "audience": {"sql": "select a.id as account_id from account a"},
    "score": {"floor": 0},
    "route": {"tiers": TIERS},
    "plays": {"t1": {}, "t2": {}, "t3": {}},
    "experiment": {"holdout_pct": 0, "unit": "account", "salt": "capacity",
                   "holdout_waiver_reason": "deterministic test fixture",
                   "primary_metric": "meeting_booked_30d"},
}


# -- the routing rule, without a database -------------------------------------

def test_a_tier_with_room_takes_the_account():
    assert enroll.resolve_tier(SPEC, {"score": 90}, occupancy={"t1": 1}) == "t1"


def test_a_full_tier_falls_through_to_the_next_one_it_qualifies_for():
    """Not refused. The account is still worked, by a cheaper play, which is
    what a routing capacity means; refusing throws away a signal already paid
    for. Tiers are not experiment arms, so this changes the play and never the
    arm."""
    assert enroll.resolve_tier(SPEC, {"score": 90}, occupancy={"t1": 2}) == "t2"


def test_it_keeps_falling_until_something_has_room():
    assert enroll.resolve_tier(SPEC, {"score": 90},
                               occupancy={"t1": 2, "t2": 3}) == "t3"


def test_an_account_never_falls_into_a_tier_it_does_not_qualify_for():
    """The thresholds descend in every shipped programme, so a t1 account also
    clears t2 — but the schema does not require that, and a cap must not push
    an account into a tier whose own predicate refuses it."""
    disjoint = {**SPEC, "route": {"tiers": [
        {"key": "high", "when": "score >= 80", "capacity_per_week": 1},
        {"key": "low", "when": "score < 80"},
    ]}}
    assert enroll.resolve_tier(disjoint, {"score": 90}, occupancy={"high": 0}) == "high"
    assert enroll.resolve_tier(disjoint, {"score": 90}, occupancy={"high": 1}) is None


def test_every_tier_full_means_no_enrolment():
    assert enroll.resolve_tier(SPEC, {"score": 90},
                               occupancy={"t1": 2, "t2": 3, "t3": 99}) == "t3", (
        "t3 declares no capacity, so no count can fill it")


def test_a_tier_declaring_no_capacity_is_never_full():
    """Absent is unlimited, not zero. Reading it as zero would stop every
    programme that does not declare one, which is most of them."""
    uncapped = {**SPEC, "route": {"tiers": [{"key": "only", "when": "score >= 0"}]}}
    assert enroll.resolve_tier(uncapped, {"score": 90},
                               occupancy={"only": 10_000}) == "only"


def test_a_capacity_of_zero_is_a_closed_tier_and_not_an_absent_one():
    """Zero is a declaration. A programme that says a tier takes nobody this
    week means it, and `if cap:` would read that as no cap at all."""
    closed = {**SPEC, "route": {"tiers": [
        {"key": "shut", "when": "score >= 80", "capacity_per_week": 0},
        {"key": "open", "when": "score >= 0"},
    ]}}
    assert enroll.resolve_tier(closed, {"score": 90}, occupancy={}) == "open"


def test_a_caller_with_no_occupancy_routes_on_predicates_alone():
    """`zolts.programtest` has no database. A programme test reports the tier a
    predicate selects, and says so rather than guessing at a loaded runtime."""
    assert enroll.resolve_tier(SPEC, {"score": 90}) == "t1"
    assert enroll.resolve_tier(SPEC, {"score": 90}, occupancy=None) == "t1"


# -- against a real database --------------------------------------------------

@requires_db
def test_the_cap_binds_at_the_routing_choke_point(db, tenant):
    """The defect as an operator meets it: a tier capped at two takes two."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        program = _publish(cur, tid, spec=SPEC, key="capacity")
        tiers = []
        for _ in range(5):
            account, _person = _account_with_contact(cur, tid)
            results = _ingest(cur, tid, account["id"], score=90,
                              dedupe=uuid.uuid4().hex)
            tiers.append(results[0].tier if results else None)

    assert tiers[:2] == ["t1", "t1"], "the first two fill the declared capacity of two"
    assert tiers[2:5] == ["t2", "t2", "t2"], (
        "and the rest fall to the next tier they qualify for, rather than being "
        "refused or admitted anyway")


@requires_db
def test_the_count_is_of_what_entered_the_tier_not_of_what_is_still_running(db, tenant):
    """A weekly allowance is spent when an account is routed, not returned when
    it exits. Counting only live enrolments would let a programme that churns
    through accounts admit far more than the cap in a week."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        program = _publish(cur, tid, spec=SPEC, key="capacity")
        account, _person = _account_with_contact(cur, tid)
        first = _ingest(cur, tid, account["id"], score=90, dedupe=uuid.uuid4().hex)
        enrollments.exit_enrollment(cur, first[0].enrollment_id, "converted")

        counts = enrollments.tier_counts_since(cur, str(program["id"]),
                                               enroll.CAPACITY_WINDOW_DAYS)
    assert counts == {"t1": 1}, "an exited enrolment still spent its place"


@requires_db
def test_an_enrolment_outside_the_window_no_longer_counts(db, tenant):
    """Rolling seven days. An account routed eight days ago is not this week's
    allowance, or the cap would be a lifetime total wearing the word *week*."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        program = _publish(cur, tid, spec=SPEC, key="capacity")
        account, _person = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=90, dedupe=uuid.uuid4().hex)
        assert enrollments.tier_counts_since(
            cur, str(program["id"]), enroll.CAPACITY_WINDOW_DAYS) == {"t1": 1}, (
            "the premise: it counts while it is inside the window")

        cur.execute("update enrollment set entered_at = now() - interval '8 days'"
                    " where program_id = %s", (str(program["id"]),))
        counts = enrollments.tier_counts_since(cur, str(program["id"]),
                                               enroll.CAPACITY_WINDOW_DAYS)
    assert counts == {}, "eight days ago is outside a seven-day window"


@requires_db
def test_the_holdout_spends_the_allowance_too(db, tenant):
    """A cap applied after the arm split would truncate the treatment arm and
    not the control one, so treatment would be the early arrivals and control
    everybody — biasing the comparison the product exists to make. The cap is
    on accounts routed to a tier; the holdout is drawn from what it admits.

    The two arms are written directly rather than enrolled through a holdout
    percentage. Assignment is a hash of the entity id, so a six-account fixture
    splits however the hashes fall: the first version of this test asserted a
    count of two and passed with the guard removed, because both accounts that
    took the tier happened to be treatment. A verdict that moves with a hash is
    a test about the hash.
    """
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        program = _publish(cur, tid, spec=SPEC, key="capacity-holdout")
        for variant in ("treatment", "control"):
            account, _person = _account_with_contact(cur, tid)
            enrollments.enroll(
                cur, tid, program_id=str(program["id"]), entity_type="account",
                entity_id=str(account["id"]), variant=variant, score=90.0,
                tier="t1", state="active" if variant == "treatment" else "control",
                context={}, next_run_at=None)
        cur.execute("select variant from enrollment where program_id = %s",
                    (str(program["id"]),))
        arms = sorted(row["variant"] for row in cur.fetchall())
        counts = enrollments.tier_counts_since(cur, str(program["id"]),
                                               enroll.CAPACITY_WINDOW_DAYS)

    assert arms == ["control", "treatment"], "the premise: one account in each arm"
    assert counts == {"t1": 2}, (
        "both arms spend the tier's allowance, or the arms are drawn from "
        "different populations and the comparison is between two things")


@requires_db
def test_a_holdout_account_is_still_routed_to_a_tier(db, tenant):
    """The end-to-end half of the claim above, asserted on what does not move:
    every enrolment carries a tier, whichever arm it landed in."""
    tid = str(tenant["id"])
    held_out = {**SPEC, "experiment": {"holdout_pct": 50, "unit": "account",
                                       "salt": "capacity-holdout-e2e",
                                       "primary_metric": "meeting_booked_30d"}}
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=held_out, key="capacity-holdout-e2e")
        enrolled = []
        for _ in range(6):
            account, _person = _account_with_contact(cur, tid)
            results = _ingest(cur, tid, account["id"], score=90,
                              dedupe=uuid.uuid4().hex)
            enrolled.extend(results)

    assert enrolled, "the premise: the fixture enrolled somebody"
    assert all(e.tier is not None for e in enrolled), (
        "a control enrolment with no tier would spend no allowance and leave the "
        "arms drawn from different populations")


@requires_db
def test_an_account_stopped_by_the_cap_is_told_apart_from_one_nobody_wanted(db, tenant):
    """A silent non-enrolment reads as a scoring problem. The operator declared
    the ceiling and is entitled to know it is what stopped this account."""
    tid = str(tenant["id"])
    one_tier = {**SPEC, "route": {"tiers": [
        {"key": "only", "when": "score >= 80", "capacity_per_week": 1}]},
        "plays": {"only": {}}}
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=one_tier, key="one-tier")
        for _ in range(2):
            account, _person = _account_with_contact(cur, tid)
            _ingest(cur, tid, account["id"], score=90, dedupe=uuid.uuid4().hex)
        cur.execute("select action, detail from audit_log"
                    " where action = 'enrollment.at_capacity'")
        rows = cur.fetchall()

    assert len(rows) == 1, "the second account was stopped by the cap and said so"
    assert rows[0]["detail"]["occupancy"] == {"only": 1}


@requires_db
def test_an_account_no_tier_wanted_is_not_reported_as_a_capacity_problem(db, tenant):
    """The other half. A score below every threshold is a scoring outcome and
    must not be dressed as a staffing one."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=SPEC, key="capacity")
        account, _person = _account_with_contact(cur, tid)
        results = _ingest(cur, tid, account["id"], score=10, dedupe=uuid.uuid4().hex)
        cur.execute("select count(*) as n from audit_log"
                    " where action = 'enrollment.at_capacity'")
        raised = int(cur.fetchone()["n"])

    assert results == [] or results[0].tier is None
    assert raised == 0


# -- what is still not enforced, and why --------------------------------------

def test_the_per_rep_capacity_is_registered_as_unenforceable():
    """Not forgotten, and not quietly enforced against something it does not
    mean. There is no representative in the data model to count against."""
    control = controls.BY_PATH["spec.route.tiers.capacity_per_week_per_rep"]
    assert not control.honoured
    assert "decision 50" in control.consequence


def test_nothing_in_the_schema_carries_a_representative():
    """The premise decision 50 rests on, asserted rather than remembered. If a
    rep ever lands on an account or an enrolment, this test fails and the
    decision is ready to close."""
    migrations = (ROOT / "runtime" / "migrations").glob("*.sql")
    text = "\n".join(path.read_text() for path in migrations)
    assert "create table user" not in text and "create table seat" not in text, (
        "an identity table now exists; decision 50 can be closed")
    for table in ("account", "enrollment"):
        block = text.split(f"create table {table} (", 1)[1].split(");", 1)[0]
        assert "owner" not in block, (
            f"{table} now carries an owner, so capacity_per_week_per_rep has "
            f"something to count against and decision 50 can be closed")


def test_the_capacity_that_is_enforced_says_where():
    control = controls.BY_PATH["spec.route.tiers.capacity_per_week"]
    assert control.honoured
    assert "enroll" in (control.enforced_by or "")
