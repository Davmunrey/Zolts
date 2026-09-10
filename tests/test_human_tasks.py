"""A step handed to a person, and whether anybody could ever say they did it.

`worker._dispatch` records a manual step as a touch with `status = 'queued'`
and `content.awaiting = 'human_review'`, then succeeds the action. Every code
path that moves a touch off `queued` lives in `inbound.py` and is driven by a
*provider* event — opened, replied, bounced. A `task` or `voice` touch has no
provider, so no such event ever arrives: the work item was created and nothing
in the system could close it (D-83).

That is also why `spec.plays.*.steps.sla_hours` was read by nothing and could
not have been (D-84). A deadline measured against a state nothing leaves
reports every task as breached for ever — a measurement that never measures,
which is the shape D-76 found in a guardrail whose control arm is structurally
zero. The SLA needed a completion before it could mean anything.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from runtime.api.app import create_app
from runtime.engine.worker import Worker
from runtime.provision import store_connection
from runtime.repo import tasks
from tests.conftest import SECRET, requires_db
from tests.test_runtime_engine import (DISPATCH_SPEC, _account_with_contact,
                                       _ingest, _publish)

# A play whose first step is real work for a person, with a deadline on it.
TASK_SPEC = {
    **DISPATCH_SPEC,
    "plays": {"t1": {"steps": [
        {"step": "call_ae", "channel": "task", "sla_hours": 4},
        {"step": "follow_up", "channel": "task", "sla_hours": 24},
    ]}},
}
NO_SLA_SPEC = {
    **DISPATCH_SPEC,
    "plays": {"t1": {"steps": [{"step": "call_ae", "channel": "task"}]}},
}


@pytest.fixture
def client(db):
    return TestClient(create_app(db, secret_key=SECRET), raise_server_exceptions=False)


@pytest.fixture
def key(db, tenant):
    from runtime.provision import issue_api_key

    return issue_api_key(db, str(tenant["id"]), "test", []).token


def _auth(token: str) -> dict[str, str]:
    return {"x-api-key": token}


@pytest.fixture
def cli_env(monkeypatch):
    """One place that declares what a subprocess-free CLI call needs.

    `Settings.from_env` builds the whole configuration before dispatching, so
    a command that touches none of these still refuses without them. A test
    that inherited them from the shell would certify the machine it ran on
    (D-31).
    """
    from tests.conftest import APP_URL, OWNER_URL

    monkeypatch.setenv("ZOLTS_DATABASE_URL", OWNER_URL)
    monkeypatch.setenv("ZOLTS_APP_DATABASE_URL", APP_URL or OWNER_URL)
    monkeypatch.setenv("ZOLTS_SECRET_KEY", SECRET)


def _queue_a_task(db, tenant, spec=None):
    """One enrolment whose first step is a human task, dispatched."""
    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=spec or TASK_SPEC)
        account, _person = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=90, dedupe=uuid.uuid4().hex)
    Worker(db, secret_key=SECRET).tick([tid])
    with db.tenant_tx(tid) as cur:
        open_now = tasks.open_tasks(cur)
    assert open_now, "the premise: dispatching the step queued work for a person"
    return tid, open_now[0]


# -- the deadline the step declared -------------------------------------------

@requires_db
def test_a_task_carries_the_deadline_its_step_declared(db, tenant):
    """`sla_hours` reached nothing at all before this. Stamped at creation
    rather than walked out of the programme at read time, so a task created
    under a four-hour promise keeps it when the programme is republished."""
    tid, task = _queue_a_task(db, tenant)
    assert task["due_at"] is not None, "the step declares sla_hours: 4"
    gap = task["due_at"] - task["created_at"]
    assert timedelta(hours=3, minutes=55) < gap < timedelta(hours=4, minutes=5), (
        f"four hours after it was created, not {gap}")


@requires_db
def test_a_step_declaring_no_sla_has_no_deadline_and_is_never_late(db, tenant):
    """Absent is not zero. A task nobody put a clock on is still work, and
    reading a missing SLA as *due immediately* would report every one of them
    as a breach the moment it was created."""
    tid, task = _queue_a_task(db, tenant, spec=NO_SLA_SPEC)
    assert task["due_at"] is None
    with db.tenant_tx(tid) as cur:
        assert tasks.overdue(cur) == []
        assert tasks.counts(cur) == {"open": 1, "overdue": 0}


@requires_db
def test_a_task_past_its_deadline_is_overdue(db, tenant):
    tid, task = _queue_a_task(db, tenant)
    with db.tenant_tx(tid) as cur:
        assert tasks.overdue(cur) == [], "the premise: it is not late yet"
        cur.execute("update touch set due_at = now() - interval '1 hour' where id = %s",
                    (task["id"],))
        late = tasks.overdue(cur)
        assert [str(r["id"]) for r in late] == [str(task["id"])]
        assert tasks.counts(cur) == {"open": 1, "overdue": 1}


@requires_db
def test_a_retry_does_not_move_the_deadline(db, tenant):
    """`record_touch` upserts on the idempotency key, so re-dispatching the
    same step reaches the same row. Letting the retry rewrite `due_at` would
    quietly extend a promise every time the work was re-queued — the same
    reasoning that keeps the mailbox that actually sent a message."""
    tid, task = _queue_a_task(db, tenant)
    from runtime.repo import ledger

    with db.tenant_tx(tid) as cur:
        again = ledger.record_touch(
            cur, tid, enrollment_id=str(task["enrollment_id"]), channel="task",
            step_key=task["step_key"], idempotency_key=task["idempotency_key"],
            content={"step": {}, "awaiting": "human_review"},
            provider=None, provider_ref=None, status="queued",
            due_at=datetime.now(timezone.utc) + timedelta(hours=720))
    assert again["id"] == task["id"], "the premise: the retry reached the same row"
    assert again["due_at"] == task["due_at"], (
        "a re-queue must not extend the deadline the work was created under")


@requires_db
def test_a_republished_programme_does_not_make_a_late_task_punctual(db, tenant):
    """The deadline belongs to the work, not to the programme as it stands
    today. Computing it from the current spec would let an operator clear a
    breach by editing the number."""
    tid, task = _queue_a_task(db, tenant)
    with db.tenant_tx(tid) as cur:
        cur.execute("update touch set due_at = now() - interval '1 hour' where id = %s",
                    (task["id"],))
        cur.execute("update program set spec = %s",
                    (json.dumps({**TASK_SPEC, "plays": {"t1": {"steps": [
                        {"step": "call_ae", "channel": "task", "sla_hours": 720}]}}}),))
        assert len(tasks.overdue(cur)) == 1


# -- the completion that did not exist ----------------------------------------

@requires_db
def test_a_person_can_say_the_work_is_done(db, tenant):
    tid, task = _queue_a_task(db, tenant)
    with db.tenant_tx(tid) as cur:
        done = tasks.complete(cur, str(task["id"]), by="rep:dana")
        assert done is not None
        assert done["completed_by"] == "rep:dana"
        assert done["completed_at"] is not None
        assert tasks.open_tasks(cur) == []
        assert tasks.counts(cur) == {"open": 0, "overdue": 0}


@requires_db
def test_completing_it_twice_records_the_first_person(db, tenant):
    """The first person to say they did the work is the record, and the SLA is
    judged against when it was finished rather than when somebody last
    clicked."""
    tid, task = _queue_a_task(db, tenant)
    with db.tenant_tx(tid) as cur:
        first = tasks.complete(cur, str(task["id"]), by="rep:dana")
        again = tasks.complete(cur, str(task["id"]), by="rep:sam")
        assert again is None, "a second completion is refused, not applied"
        cur.execute("select completed_by, completed_at from touch where id = %s",
                    (task["id"],))
        row = cur.fetchone()
    assert row["completed_by"] == "rep:dana"
    assert row["completed_at"] == first["completed_at"]


@requires_db
def test_only_a_human_task_can_be_completed(db, tenant):
    """A sent email is not work waiting for a person, and marking one done
    would put a completion on a row whose whole meaning comes from a
    provider."""
    tid, task = _queue_a_task(db, tenant)
    with db.tenant_tx(tid) as cur:
        cur.execute(
            "insert into touch (tenant_id, channel, idempotency_key, status, content)"
            " values (%s,'email',%s,'sent','{}') returning id",
            (tid, f"e-{uuid.uuid4().hex}"))
        email_id = str(cur.fetchone()["id"])
        assert tasks.complete(cur, email_id, by="rep:dana") is None


@requires_db
def test_a_task_that_was_finished_late_says_so(db, tenant):
    tid, task = _queue_a_task(db, tenant)
    with db.tenant_tx(tid) as cur:
        cur.execute("update touch set due_at = now() - interval '1 hour' where id = %s",
                    (task["id"],))
        done = tasks.complete(cur, str(task["id"]), by="rep:dana")
    assert done["completed_at"] > done["due_at"]


# -- the surfaces -------------------------------------------------------------

@requires_db
def test_the_api_lists_the_work_and_takes_the_completion(db, tenant, client, key):
    tid, task = _queue_a_task(db, tenant)
    listed = client.get("/v1/tasks", headers=_auth(key))
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["counts"] == {"open": 1, "overdue": 0}
    assert [t["id"] for t in body["tasks"]] == [str(task["id"])]
    assert body["tasks"][0]["late"] is False

    done = client.post(f"/v1/tasks/{task['id']}/complete", headers=_auth(key))
    assert done.status_code == 200, done.text
    assert done.json()["completedBy"].startswith("key:")

    again = client.post(f"/v1/tasks/{task['id']}/complete", headers=_auth(key))
    assert again.status_code == 409, "a second completion is a conflict, not a 200"


@requires_db
def test_another_tenants_task_cannot_be_completed(db, tenant, other_tenant, client, key,
                                                  app_role_is_restricted):
    """The same 409 as an unknown id. Whether a task exists in somebody else's
    tenant is not something a guess should be able to tell apart."""
    _tid, task = _queue_a_task(db, other_tenant)
    refused = client.post(f"/v1/tasks/{task['id']}/complete", headers=_auth(key))
    assert refused.status_code == 409
    with db.tenant_tx(str(other_tenant["id"])) as cur:
        assert len(tasks.open_tasks(cur)) == 1, "and it is still open over there"


@requires_db
def test_the_command_line_lists_the_work_and_records_who_did_it(db, tenant, capsys,
                                                              cli_env):
    """The operator's surface. The console view is deliberately not in this
    change: the screenshot check that would prove it cannot run in this
    container, and this file is where three overlapping-text defects came from
    (D-38). An unverified screen is worse than a documented gap."""
    from runtime import cli

    tid, task = _queue_a_task(db, tenant)
    assert cli.main(["tasks", "--tenant", tid]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["counts"] == {"open": 1, "overdue": 0}
    assert listed["tasks"][0]["step"] == "call_ae"

    assert cli.main(["task-done", "--tenant", tid, "--task", str(task["id"]),
                     "--by", "Dana Cruz"]) == 0
    recorded = json.loads(capsys.readouterr().out)
    assert recorded["completedBy"] == "Dana Cruz"
    assert recorded["late"] is False

    assert cli.main(["task-done", "--tenant", tid, "--task", str(task["id"]),
                     "--by", "Sam"]) == 1, "a second completion exits non-zero"


@requires_db
def test_the_completion_is_in_the_audit_log(db, tenant, cli_env):
    """Who said the work was done, and whether it was late. A completion an
    audit cannot see is one nobody can be held to."""
    from runtime import cli

    tid, task = _queue_a_task(db, tenant)
    with db.tenant_tx(tid) as cur:
        cur.execute("update touch set due_at = now() - interval '1 hour' where id = %s",
                    (task["id"],))
    assert cli.main(["task-done", "--tenant", tid, "--task", str(task["id"]),
                     "--by", "Dana Cruz"]) == 0
    with db.tenant_tx(tid) as cur:
        cur.execute("select actor, detail from audit_log where action = 'task.completed'")
        row = cur.fetchone()
    assert row["actor"] == "Dana Cruz"
    assert row["detail"]["late"] is True
