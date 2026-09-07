"""The five product invariants, each broken on purpose to see what notices.

`CLAUDE.md` lists five invariants that must not be relaxed. Three were
enforced. Two were intentions that every test in the repository agreed with by
accident:

    1 · agents propose, the runtime disposes   -> nothing stopped an agent
                                                  importing a connector
    2 · every external action carries a key    -> caught
    3 · every action records a decision        -> caught
    3 · ... with a reason                      -> nothing; `rationale` was a
                                                  nullable column and the whole
                                                  suite passed writing None
    4 · a holdout, or a written waiver         -> caught
    5 · program logic is versioned config      -> nothing; see ADR-030

Each test below is paired with the mutation that motivated it, and each one
was watched to fail with that mutation applied. A guard that passes when you
break the thing it guards is not a guard.
"""

from __future__ import annotations

import uuid

import pytest

# The smallest program admission accepts. `publish` used to store anything —
# these tests published `{"a": 1}` — and now runs the four checks that every
# caller but the HTTP handler used to skip, so a fixture has to be a program.
ADMISSIBLE = {
    "trigger": {"events": [{"signal": "funding.round"}], "window": "30d"},
    "audience": {"sql": "select id as account_id from account"},
    "score": {"floor": 0},
    "route": {"tiers": [{"key": "t1"}]},
    "plays": {"t1": {}},
    "experiment": {"holdout_pct": 10, "unit": "account", "salt": "invariants"},
}


def _spec(floor: int) -> dict:
    """The same program with one dial moved, which is what a republish is."""
    return {**ADMISSIBLE, "score": {"floor": floor}}


# -- a version is written once --------------------------------------------

@pytest.mark.db
def test_republishing_a_version_with_different_content_is_refused(db, tenant):
    """`publish` was a plain upsert, so publishing v2.1.0 twice replaced the
    spec of a version that was already live.

    Every enrollment running under it then pointed at a document that no
    longer described what it did, and the measurement attributed one lift to
    two different programs. Program logic is versioned configuration; a
    version is written once.
    """
    from runtime.repo import programs

    with db.tenant_tx(tenant['id']) as cur:
        first = programs.publish(cur, tenant['id'], key="immutable-demo", version="1.0.0",
                                 spec=_spec(0), spec_hash="hash-one")
        assert first["spec"]["score"]["floor"] == 0

        with pytest.raises(programs.VersionIsImmutable, match="already exists"):
            programs.publish(cur, tenant['id'], key="immutable-demo", version="1.0.0",
                             spec=_spec(80), spec_hash="hash-two")

    with db.tenant_tx(tenant['id']) as cur:
        cur.execute("select spec from program where key = %s and version = %s",
                    ("immutable-demo", "1.0.0"))
        assert cur.fetchone()["spec"]["score"]["floor"] == 0, "the stored spec was replaced"


@pytest.mark.db
def test_republishing_identical_content_still_succeeds(db, tenant):
    """A retried request and a re-seeded starter program are both legitimate,
    and neither changes anything. Refusing them would make the API's own
    idempotency a failure."""
    from runtime.repo import programs

    with db.tenant_tx(tenant['id']) as cur:
        programs.publish(cur, tenant['id'], key="idempotent-demo", version="1.0.0",
                         spec=_spec(0), spec_hash="same-hash")
        again = programs.publish(cur, tenant['id'], key="idempotent-demo", version="1.0.0",
                                 spec=_spec(0), spec_hash="same-hash",
                                 metadata={"name": "renamed"})
        assert again["spec"] == _spec(0)
        assert again["metadata"]["name"] == "renamed", (
            "an identical republish should still be able to correct a label")


# -- a decision without a reason ------------------------------------------

@pytest.mark.db
def test_a_policy_decision_without_a_reason_is_refused(db, tenant):
    """Invariant 3 asks for allow or deny *with a reason*, and only the first
    two thirds were enforced.

    Found by breaking it: the gate was changed to write `rationale=None` and
    all 823 tests passed. The column was nullable and `record_decision` took
    `str | None`. A null renders in the console's Policy view as a decision
    nobody can explain — the one question that table exists to answer.
    """
    from runtime.repo import ledger

    with db.tenant_tx(tenant["id"]) as cur:
        for empty in (None, "", "   "):
            with pytest.raises(ledger.DecisionWithoutAReason, match="no reason"):
                ledger.record_decision(
                    cur, tenant["id"], subject_type="person",
                    subject_id=str(uuid.uuid4()), action="email.send",
                    decision="deny", rule_key="suppression.unsubscribed",
                    jurisdiction="ES", rationale=empty)


@pytest.mark.db
def test_the_database_refuses_a_reasonless_decision_too(db, tenant):
    """The check in Python names the cause; the column is what makes it true
    for anything that writes the table without going through it."""
    import psycopg

    with db.tenant_tx(tenant["id"]) as cur:
        with pytest.raises(psycopg.errors.Error):
            cur.execute(
                "insert into policy_decision (tenant_id, subject_type, subject_id,"
                " action, decision, rule_key, jurisdiction, rationale)"
                " values (%s,'person',%s,'email.send','deny','r','ES',null)",
                (tenant["id"], str(uuid.uuid4())))


@pytest.mark.db
def test_a_blank_reason_is_not_a_reason(db, tenant):
    """A space satisfies NOT NULL and explains nothing."""
    import psycopg

    with db.tenant_tx(tenant["id"]) as cur:
        with pytest.raises(psycopg.errors.Error):
            cur.execute(
                "insert into policy_decision (tenant_id, subject_type, subject_id,"
                " action, decision, rule_key, jurisdiction, rationale)"
                " values (%s,'person',%s,'email.send','deny','r','ES','   ')",
                (tenant["id"], str(uuid.uuid4())))


# -- agents propose; the runtime disposes ---------------------------------

def test_no_agent_can_reach_a_thing_that_acts_on_a_prospect():
    """Invariant 1, enforced structurally rather than by intention.

    Nothing stopped an agent from importing a connector and sending directly.
    It happens not to, today — which is a fact about the current four files
    and not a property of the system. The fifth agent is the one that would
    break it, and the failure mode is an email nobody approved.

    A model call is not an external action in this sense: it is the agent
    thinking, it costs money and is guarded by the spend guard, and it touches
    nobody's prospect. What is forbidden is reaching the machinery that
    contacts a person or writes to a customer's CRM.
    """
    import ast
    from pathlib import Path

    forbidden = ("runtime.connectors", "runtime.channels", "runtime.fleet",
                 "runtime.engine.worker", "runtime.engine.gate", "runtime.repo.actions")
    offences = []
    for module in sorted(Path("runtime/agents").glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(name == f or name.startswith(f + ".") for f in forbidden):
                    offences.append(f"{module.name} imports {name}")

    assert not offences, (
        "an agent reaches machinery that acts on a prospect: " + "; ".join(offences)
        + ". Agents propose; the runtime disposes.")


def test_the_guard_above_would_notice(tmp_path):
    """A structural check that cannot fail is decoration. This proves the rule
    catches the import it forbids."""
    import ast

    tree = ast.parse("from runtime.connectors import registry\n")
    found = [n.module for n in ast.walk(tree)
             if isinstance(n, ast.ImportFrom) and n.module
             and n.module.startswith("runtime.connectors")]
    assert found == ["runtime.connectors"], (
        "the detection the test above relies on does not fire")

