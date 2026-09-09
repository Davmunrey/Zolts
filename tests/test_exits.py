"""The exit block every programme declares, which the runtime never evaluated.

`runtime/engine/planner.apply_exits` reads `spec.exit` and nothing else does.
It had one caller in the whole repository and that caller was a test, so every
shipped archetype's exit block was declarative content: nothing exited on
`outcome.type in (...)` and nothing on `days_in_program > 45` (D-77).

It stayed invisible because the two exits that *do* fire cover the ends of a
sequence — `sequence_complete` when the steps run out, and `opted_out` from
`inbound._opt_out` — leaving the case in between silent. That case is a person
who books a meeting and keeps receiving the rest of the sequence, which is the
automation a buyer points at when they say these tools embarrass them.

The second half is smaller and worse. `zolts/dsl.py` linted for an exit rule
setting `suppress`, reporting *opt-outs would not be recorded* when none did.
The only rule satisfying it in all four archetypes tested
`outcome.type == 'unsubscribe'`, and nothing writes an outcome of that type at
all: triage routes that verdict to the suppression path before any outcome is
recorded. So a check that certified opt-outs were handled was answered by a
rule that could not fire.
"""

from __future__ import annotations

import ast
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from runtime.agents import triage
from runtime.engine import planner
from runtime.engine.worker import Worker
from runtime.provision import store_connection
from runtime.repo import actions, enrollments, entities, programs
from tests.conftest import SECRET, requires_db
from tests.test_runtime_engine import (DISPATCH_SPEC, _account_with_contact,
                                       _ingest, _publish)
from zolts import dsl, metrics

ROOT = Path(__file__).resolve().parent.parent
PROGRAMS = sorted((ROOT / "examples" / "programs").glob("*.yaml"))


@pytest.fixture
def client(db):
    from fastapi.testclient import TestClient

    from runtime.api.app import create_app

    return TestClient(create_app(db, secret_key=SECRET), raise_server_exceptions=False)


@pytest.fixture
def endpoint(db, tenant):
    from runtime.provision import create_webhook_endpoint

    return create_webhook_endpoint(db, str(tenant["id"]), provider="smartlead",
                                   secret_key=SECRET)


# -- the rule that could not fire ---------------------------------------------

def test_the_runtime_records_no_outcome_called_unsubscribe():
    """The premise the whole defect rests on, asserted rather than asserted of.

    Every shipped archetype declared `outcome.type == 'unsubscribe'`. Triage
    has that verdict, and `inbound._reply` routes it to `_opt_out` *before*
    writing an outcome — which is correct, and which is why the rule was dead.
    """
    assert "unsubscribe" in triage.VERDICTS, "the verdict exists"
    assert "unsubscribe" not in dsl.RECORDED_OUTCOMES, "the outcome type does not"
    assert "reply_unsubscribe" not in dsl.RECORDED_OUTCOMES


def test_the_recorded_outcomes_are_what_the_runtime_actually_writes():
    """Two lists that must agree, and `zolts/` cannot import `runtime/` to
    share one. So the agreement is measured here rather than remembered: an
    outcome type added to the runtime and not to the lint would make the lint
    reject a rule that works."""
    written = set(metrics.OUTCOME_TYPES) | {
        f"reply_{verdict}" for verdict in triage.VERDICTS if verdict != "unsubscribe"}
    assert dsl.RECORDED_OUTCOMES == written, (
        "the lint's idea of what the runtime records has drifted from what it records")


@pytest.mark.parametrize("when,expected", [
    ("outcome.type == 'unsubscribe'", ["unsubscribe"]),
    ("outcome.type in ('reply_positive','churn')", ["churn"]),
    ("outcome.type == 'reply_negative'", []),
    ("outcome.type in ('reply_positive','meeting','opp_created')", []),
    ("days_in_program > 45", []),
    ("engagement.converted > 0", []),
])
def test_the_lint_names_an_outcome_nothing_records(when, expected):
    assert dsl._outcomes_named(when) == expected


@pytest.mark.parametrize("path", PROGRAMS, ids=lambda p: p.stem)
def test_every_shipped_exit_rule_can_fire(path):
    """The content half. All four declared a rule naming an outcome nothing
    writes; the e-commerce archetype declared a second one, `purchase`, which
    the lint found on its first run."""
    spec = yaml.safe_load(path.read_text())["spec"]
    for rule in spec.get("exit") or []:
        dead = dsl._outcomes_named(rule.get("when") or "")
        assert not dead, f"{path.name}: '{rule.get('reason')}' tests for {dead}"


# -- the caller that did not exist --------------------------------------------

def _calls(module: Path) -> set[str]:
    tree = ast.parse(module.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                out.add(func.attr)
            elif isinstance(func, ast.Name):
                out.add(func.id)
    return out


def test_the_tick_applies_the_exit_rules_before_it_plans():
    """A guard correct, tested and not on the path is the third shape in
    `docs/22`, and this was it: `apply_exits` worked and no shipped code
    reached it."""
    assert "apply_exits" in _calls(ROOT / "runtime" / "engine" / "worker.py"), (
        "the planning loop does not evaluate the programme's exit rules")


def test_the_outcome_path_applies_them_too():
    """The tick alone is not enough. `plan_next` queues the following step with
    its wait already applied, so by the time a reply is read the next email is
    in the outbox with a due time of its own — and a tick-time exit runs after
    it has gone out."""
    source = (ROOT / "runtime" / "engine" / "inbound.py").read_text()
    assert "apply_exits" in _calls(ROOT / "runtime" / "engine" / "inbound.py")
    tree = ast.parse(source)
    outcome = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_outcome")
    assert "_exits" in _calls_in(outcome), (
        "the exit rules must be applied where the outcome is recorded")


def _calls_in(node: ast.AST) -> set[str]:
    out: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
            out.add(child.func.id)
    return out


# -- what it does now ---------------------------------------------------------

CONVERTED = {"when": "outcome.type in ('reply_positive','meeting','opp_created')",
             "reason": "converted"}
DECLINED = {"when": "outcome.type == 'reply_negative'", "reason": "declined",
            "suppress": True}
EXHAUSTED = {"when": "days_in_program > 45", "reason": "exhausted"}
SPEC = {**DISPATCH_SPEC, "exit": [CONVERTED, DECLINED, EXHAUSTED]}


def _running(db, tenant, fake, spec=None):
    """One enrollment with its next step already queued, as a real one is."""
    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=spec or SPEC)
        account, person = _account_with_contact(cur, tid)
        results = _ingest(cur, tid, account["id"], score=70)
    Worker(db, secret_key=SECRET).tick([tid])
    with db.tenant_tx(tid) as cur:
        cur.execute("select idempotency_key, enrollment_id from touch")
        touch = cur.fetchone()
        enrollment = enrollments.get(cur, results[0].enrollment_id)
        program = programs.get(cur, str(enrollment["program_id"]))
        cur.execute("update enrollment set next_run_at = now() where id = %s",
                    (enrollment["id"],))
        planner.plan_next(cur, tid, enrollments.get(cur, results[0].enrollment_id), program)
        assert actions.pending_count(cur) >= 1, (
            "the premise: there is a queued next step for an exit to cancel")
    return tid, person, dict(touch), str(enrollment["id"])


@requires_db
def test_a_positive_reply_ends_the_sequence_and_cancels_the_next_step(
        db, tenant, fake, client, endpoint):
    """The defect, as the prospect experiences it. Before this, an account that
    replied kept receiving the rest of the sequence: the *converted* rule every
    shipped archetype declares was evaluated by nothing."""
    from tests.test_runtime_webhooks import _post

    tid, person, touch, enrollment_id = _running(db, tenant, fake)
    response = _post(client, endpoint, {
        "event_type": "reply", "id": f"evt-{uuid.uuid4().hex}",
        "to_email": str(person["email"]),
        "lead": {"custom_fields": {"zolts_idempotency_key": touch["idempotency_key"]}}})
    assert response.status_code == 202, response.text
    effects = set(response.json()["applied"][0]["effects"])
    assert "outcome.reply_positive" in effects
    assert "enrollment.exited:converted" in effects

    with db.tenant_tx(tid) as cur:
        assert enrollments.get(cur, enrollment_id)["exit_reason"] == "converted"
        assert actions.pending_count(cur) == 0, "tomorrow's step must not still be queued"
        assert not entities.suppressed_keys(cur, str(person["email"]), None), (
            "converting is not a reason to stop contacting somebody")


def _negative(text: str = "Not interested, please stop."):
    """A triage reading, supplied rather than generated.

    `inbound.apply` takes the verdict as an argument, so the test does not need
    a model provider — which matters, because a test that skipped without one
    would leave `suppress` unexercised on every machine that has none, and an
    unexercised guard is the thing this file is about.
    """
    from runtime.agents.triage import SpendVerdict, Triage

    return Triage(verdict="negative", quote=text, text=text,
                  spend=SpendVerdict("yes", 0.0, None, None), completion=None,
                  model="test")


@requires_db
def test_an_explicit_no_also_suppresses_the_contact(db, tenant, fake):
    """What `suppress` is for, and it had no reader either: `apply_exits` never
    looked at the flag. The only rule declaring it named an outcome nothing
    writes, so the field and its one use were dead together."""
    from runtime.engine import inbound

    tid, person, touch, enrollment_id = _running(db, tenant, fake)
    with db.tenant_tx(tid) as cur:
        event = inbound.store(cur, tid, provider="smartlead", signature_ok=True, payload={
            "event_type": "reply", "id": f"evt-{uuid.uuid4().hex}",
            "to_email": str(person["email"]),
            "reply_message": {"text": "Not interested, please stop."},
            "lead": {"custom_fields": {
                "zolts_idempotency_key": touch["idempotency_key"]}}})
        result = inbound.apply(cur, tid, event, triage=_negative())

    effects = set(result.effects)
    assert "outcome.reply_negative" in effects, "the premise: triage read it as a no"
    assert "enrollment.exited:declined" in effects
    assert "suppression.email" in effects
    with db.tenant_tx(tid) as cur:
        assert enrollments.get(cur, enrollment_id)["exit_reason"] == "declined"
        assert entities.suppressed_keys(cur, str(person["email"]), None)
        assert actions.pending_count(cur) == 0


@requires_db
def test_a_declared_suppress_that_cannot_be_honoured_says_so(db, tenant, fake):
    """Named rather than passed over. A control that quietly does nothing when
    it cannot find its subject is a promise kept by luck, which is the family
    this whole register is made of."""
    from runtime.engine import inbound

    tid, _person, touch, _enrollment_id = _running(db, tenant, fake)
    with db.tenant_tx(tid) as cur:
        event = inbound.store(cur, tid, provider="smartlead", signature_ok=True, payload={
            "event_type": "reply", "id": f"evt-{uuid.uuid4().hex}",
            "reply_message": {"text": "Not interested."},
            "lead": {"custom_fields": {
                "zolts_idempotency_key": touch["idempotency_key"]}}})
        result = inbound.apply(cur, tid, event, triage=_negative())

    effects = set(result.effects)
    assert "enrollment.exited:declined" in effects, "the exit still happens"
    assert "suppression.unattributed" in effects, (
        "and the operator is told the suppression had no address to write")
    assert "suppression.email" not in effects


@requires_db
def test_replaying_one_stored_event_does_not_exit_twice(db, tenant, fake):
    """`record_outcome` deduplicates on the provider's event id, and the exit
    is applied inside that branch. `apply` is documented as safe to re-run on a
    replay, and an exit outside that branch would fire again on every replay —
    suppressing a contact twice and writing a second audit line for one event.

    The same *stored* event, applied twice, on purpose. Posting the webhook
    twice would prove nothing: `store` deduplicates on the provider's id before
    `apply` is reached, so the second request never gets as far as the code
    under test. That version of this test passed with the guard removed.
    """
    from runtime.engine import inbound

    tid, person, touch, enrollment_id = _running(db, tenant, fake)
    with db.tenant_tx(tid) as cur:
        event = inbound.store(cur, tid, provider="smartlead", signature_ok=True, payload={
            "event_type": "reply", "id": f"evt-{uuid.uuid4().hex}",
            "to_email": str(person["email"]),
            "lead": {"custom_fields": {
                "zolts_idempotency_key": touch["idempotency_key"]}}})
        first = set(inbound.apply(cur, tid, event).effects)
        assert "enrollment.exited:converted" in first, "the premise: the first exits"
        replayed = set(inbound.apply(cur, tid, event).effects)

    assert "outcome.reply_positive" not in replayed, "the outcome deduplicates"
    assert "enrollment.exited:converted" not in replayed, (
        "and the exit rides on that dedupe rather than re-firing")
    with db.tenant_tx(tid) as cur:
        cur.execute("select count(*) as n from outcome where enrollment_id = %s",
                    (enrollment_id,))
        assert cur.fetchone()["n"] == 1


@requires_db
def test_an_exhausted_enrollment_exits_on_the_tick(db, tenant, fake):
    """The rule no outcome triggers, and the reason the tick needs the call as
    well as the outcome path."""
    tid, _person, _touch, enrollment_id = _running(db, tenant, fake)
    with db.tenant_tx(tid) as cur:
        cur.execute("update enrollment set entered_at = %s, next_run_at = now(),"
                    " state = 'running', exit_reason = null, exited_at = null"
                    " where id = %s",
                    (datetime.now(timezone.utc) - timedelta(days=46), enrollment_id))
        cur.execute("update action set state = 'cancelled' where enrollment_id = %s",
                    (enrollment_id,))
    Worker(db, secret_key=SECRET).plan_due(tid)
    with db.tenant_tx(tid) as cur:
        assert enrollments.get(cur, enrollment_id)["exit_reason"] == "exhausted"


@requires_db
def test_a_tick_does_not_fire_a_rule_about_an_outcome_that_has_not_happened(
        db, tenant, fake):
    """`zolts.expr` answers a comparison against an absent field with False, so
    a rule about an outcome is a non-match on the tick rather than an error —
    the posture D-37 set for a payload that cannot answer its own predicate."""
    tid, _person, _touch, enrollment_id = _running(db, tenant, fake)
    with db.tenant_tx(tid) as cur:
        cur.execute("select count(*) as n from outcome where enrollment_id = %s",
                    (enrollment_id,))
        assert cur.fetchone()["n"] == 0, "the premise: nothing has converted"
        cur.execute("update enrollment set next_run_at = now() where id = %s",
                    (enrollment_id,))
    Worker(db, secret_key=SECRET).plan_due(tid)
    with db.tenant_tx(tid) as cur:
        # Not `is None`: a short fixture sequence reaches its end and exits as
        # `sequence_complete`, which is the planner doing its job and not an
        # exit rule firing. The claim is about the rules.
        assert enrollments.get(cur, enrollment_id)["exit_reason"] not in {
            "converted", "declined", "exhausted"}


@requires_db
def test_an_exit_rule_reads_the_same_engagement_a_step_does(db, tenant, fake):
    """One vocabulary for the same facts. A second set of names for what a
    contact has done is a second set of rules to get wrong."""
    spec = {**DISPATCH_SPEC,
            "exit": [{"when": "engagement.sent > 0", "reason": "touched"}]}
    tid, _person, _touch, enrollment_id = _running(db, tenant, fake, spec=spec)
    with db.tenant_tx(tid) as cur:
        cur.execute("update enrollment set next_run_at = now() where id = %s",
                    (enrollment_id,))
    Worker(db, secret_key=SECRET).plan_due(tid)
    with db.tenant_tx(tid) as cur:
        assert enrollments.get(cur, enrollment_id)["exit_reason"] == "touched"
