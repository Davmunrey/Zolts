"""Bulk, with reasons: one act over many rows, one written reason, and a
refusal that names the rows it refused.

An operator with forty drafts does not click forty times; they open a
spreadsheet instead, and the product loses the day (`docs/28`, OX-7). The
exit criterion: a batch of three where one row is stale returns two done and
one named refusal, and the two are recorded with the batch's reason.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from runtime import batches as runner
from tests.conftest import requires_db
from zolts.batches import ACTS, MAX_BATCH, BatchRefusal, dedupe, refuse_batch, summarise
from zolts.reasons import ReasonRefusal

REASON = "the legal review cleared this campaign this morning"
NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


# -- the rule ------------------------------------------------------------


def test_a_row_named_twice_is_one_row_in_order():
    assert dedupe(["b", "a", " b ", "", "a", "c"]) == ["b", "a", "c"]


def test_the_batch_itself_is_refused_before_any_row_for_three_reasons():
    assert refuse_batch("proposals", "approve", [], REASON) == BatchRefusal.NO_ROWS.value
    assert refuse_batch("proposals", "approve", ["a"] * 3, REASON) is None, "duplicates fold"
    too_many = [f"id-{i}" for i in range(MAX_BATCH + 1)]
    assert refuse_batch("proposals", "approve", too_many, REASON) == BatchRefusal.TOO_MANY.value
    assert refuse_batch("proposals", "destroy", ["a"], REASON) == BatchRefusal.UNKNOWN_ACT.value
    assert refuse_batch("outbox", "approve", ["a"], REASON) == BatchRefusal.UNKNOWN_ACT.value


def test_a_batch_needs_a_reason_that_says_something_like_every_override():
    assert refuse_batch("tasks", "complete", ["a"], "") == ReasonRefusal.NO_REASON.value
    assert refuse_batch("tasks", "complete", ["a"], "done") == ReasonRefusal.REASON_TOO_SHORT.value
    assert (refuse_batch("tasks", "complete", ["a"], "done done done")
            == ReasonRefusal.REASON_SAYS_NOTHING.value)
    assert refuse_batch("tasks", "complete", ["a"], REASON) is None


def test_every_act_a_per_row_endpoint_offers_is_a_batch_act_and_no_other():
    assert ACTS == {"proposals": ("approve", "reject"), "tasks": ("complete",),
                    "outbox": ("revive", "discard")}


def test_the_outcome_names_the_refused_rows_and_counts_both():
    out = summarise("proposals", "approve", f" {REASON} ", "b1",
                    [("a", None), ("b", "already_dispatched"), ("c", None)])
    assert out.done == ("a", "c") and out.refused == {"b": "already_dispatched"}
    assert out.reason == REASON
    assert out.as_dict()["counts"] == {"done": 2, "refused": 1}


# -- against a real database ---------------------------------------------


def _proposal(cur, tid, state="needs_human"):
    cur.execute(
        "insert into proposal (tenant_id, agent, idempotency_key, channel, step_key, model,"
        " prompt_version, content, evidence, eval, eval_score, spend, cost_micros, state)"
        " values (%s,'copywriter',%s,'email','email_1','m','v1',%s,'[]','{}',0.8,'{}',100,%s)"
        " returning id",
        (tid, uuid.uuid4().hex, json.dumps({"body": "Congratulations on the round."}), state))
    return str(cur.fetchone()["id"])


def _task(cur, tid):
    cur.execute(
        "insert into touch (tenant_id, channel, step_key, idempotency_key, status, content,"
        " due_at, direction) values (%s,'task','call_revops',%s,'queued',%s,%s,'out')"
        " returning id",
        (tid, uuid.uuid4().hex, json.dumps({"awaiting": "human_review", "brief": "call"}),
         NOW + timedelta(hours=1)))
    return str(cur.fetchone()["id"])


def _dead(cur, tid, state="dead"):
    cur.execute(
        "insert into action (tenant_id, kind, channel, step_key, idempotency_key, payload,"
        " state, attempts, max_attempts, last_error)"
        " values (%s,'send','email','email_1',%s,'{}',%s,5,5,'401 invalid_grant') returning id",
        (tid, uuid.uuid4().hex, state))
    return str(cur.fetchone()["id"])


def _audit(cur, action):
    cur.execute("select subject, detail from audit_log where action = %s order by at",
                (action,))
    return [(str(r["subject"]), r["detail"]) for r in cur.fetchall()]


@requires_db
def test_two_done_and_one_named_refusal_recorded_with_the_batchs_reason(db, tenant):
    """The exit criterion of OX-7, on proposals: the stale row was dispatched
    by a colleague a second earlier."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        fresh, stale, other = _proposal(cur, tid), _proposal(cur, tid, "dispatched"), _proposal(cur, tid)
        out = runner.run(cur, tid, kind="proposals", act="approve",
                         ids=[fresh, stale, other], reason=REASON, actor="key:test")
        approved = _audit(cur, "proposal.approved")
        cur.execute("select id, state from proposal order by created_at")
        states = {str(r["id"]): r["state"] for r in cur.fetchall()}
    assert out.done == (fresh, other)
    assert out.refused == {stale: "already_dispatched"}
    assert states[fresh] == "dispatched" and states[other] == "dispatched"
    # Two rows written in one transaction share its timestamp, so the order
    # between them is not a fact; the set is.
    assert {s for s, _ in approved} == {fresh, other}
    assert all(d["reason"] == REASON and d["batch"] == out.batch_id for _, d in approved), (
        "the two are recorded with the batch's reason and its id")


@requires_db
def test_a_failing_row_rolls_back_alone_and_the_rest_proceed(db, tenant):
    """A row that is not even a uuid makes the database raise; the savepoint
    contains it, and the audit row for the batch is still written."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        good = _proposal(cur, tid)
        out = runner.run(cur, tid, kind="proposals", act="reject",
                         ids=[good, "not-a-uuid"], reason=REASON, actor="key:test")
        batches = _audit(cur, "proposals.batch")
    assert out.done == (good,)
    assert out.refused["not-a-uuid"].startswith("failed: ")
    assert len(batches) == 1 and batches[0][1]["refused"] == out.refused


@requires_db
def test_tasks_and_dead_actions_batch_the_same_way(db, tenant):
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        open_task, other_task = _task(cur, tid), _task(cur, tid)
        done_first = runner.run(cur, tid, kind="tasks", act="complete", ids=[open_task],
                                reason=REASON, actor="key:test")
        again = runner.run(cur, tid, kind="tasks", act="complete",
                           ids=[open_task, other_task], reason=REASON, actor="key:test")
        dead, alive = _dead(cur, tid), _dead(cur, tid, state="pending")
        revived = runner.run(cur, tid, kind="outbox", act="revive", ids=[dead, alive],
                             reason=REASON, actor="key:test")
        completed = _audit(cur, "task.completed")
    assert done_first.done == (open_task,)
    assert again.done == (other_task,) and again.refused == {open_task: "not_a_task_or_already_done"}
    assert revived.done == (dead,) and revived.refused == {alive: "not_dead"}
    assert all(d["reason"] == REASON for _, d in completed)


@requires_db
def test_a_refused_batch_does_nothing_and_says_why(db, tenant):
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        row = _proposal(cur, tid)
        with pytest.raises(runner.BatchRefused) as refused:
            runner.run(cur, tid, kind="proposals", act="approve", ids=[row], reason="ok",
                       actor="key:test")
        cur.execute("select state from proposal where id = %s", (row,))
        state = cur.fetchone()["state"]
    assert refused.value.key == ReasonRefusal.REASON_TOO_SHORT.value
    assert state == "needs_human"


@requires_db
def test_the_three_batch_endpoints_answer_over_http(db, tenant):
    from fastapi.testclient import TestClient

    from runtime.api.app import create_app
    from runtime.provision import issue_api_key

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        a, b = _proposal(cur, tid), _proposal(cur, tid, "rejected")
        task = _task(cur, tid)
        dead = _dead(cur, tid)
    client = TestClient(create_app(db), raise_server_exceptions=False)
    token = issue_api_key(db, tid, "test", ["approve"]).token
    headers = {"x-api-key": token}

    answer = client.post("/v1/proposals/batch", headers=headers,
                         json={"act": "approve", "ids": [a, b], "reason": REASON})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["done"] == [a] and body["refused"] == {b: "already_rejected"}
    assert body["counts"] == {"done": 1, "refused": 1}

    tasks = client.post("/v1/tasks/batch", headers=headers,
                        json={"act": "complete", "ids": [task], "reason": REASON})
    assert tasks.status_code == 200 and tasks.json()["done"] == [task]

    outbox = client.post("/v1/outbox/batch", headers=headers,
                         json={"act": "discard", "ids": [dead], "reason": REASON})
    assert outbox.status_code == 200 and outbox.json()["done"] == [dead]

    thin = client.post("/v1/proposals/batch", headers=headers,
                       json={"act": "approve", "ids": [a], "reason": "ok"})
    assert thin.status_code == 422 and thin.json()["detail"]["refused"] == "reason_too_short"
    wrong = client.post("/v1/proposals/batch", headers=headers,
                        json={"act": "complete", "ids": [a], "reason": REASON})
    assert wrong.status_code == 422 and wrong.json()["detail"]["refused"] == "unknown_act"

    reader = issue_api_key(db, tid, "reader", ["read"]).token
    denied = client.post("/v1/tasks/batch", headers={"x-api-key": reader},
                         json={"act": "complete", "ids": [task], "reason": REASON})
    assert denied.status_code == 403
