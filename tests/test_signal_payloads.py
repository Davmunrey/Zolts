"""What the runtime does with a payload it cannot read.

`POST /v1/signals` is the front door for data this runtime does not control.
The trigger predicate comments say so:

    A `where` clause that references an absent field evaluates to False rather
    than raising: signal payloads are external data and a partial one is a
    non-match, not an outage.

True of an absent field, and untrue of a present one that cannot answer the
question. `payload.amount_usd >= 5000000` against `"9000000"` — the same
number, quoted, which is what a great many JSON producers send and what every
form-encoded webhook sends — raised `TypeError` and came out of the endpoint
as **500**. D-37.

The three cases are deliberately different, and the difference is the point:

| Payload | Answer | Told to whom |
|---|---|---|
| the field, correctly typed | matches or does not | nobody: this is the normal path |
| the field absent | non-match | nobody: a partial payload is a non-match by design |
| the field present and unusable | non-match | **the sender, in the response, and the operator, in the audit log** |
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from runtime.api.app import create_app
from runtime.engine import triggers
from runtime.provision import issue_api_key
from runtime.repo import entities, programs
from tests.conftest import requires_db

WHERE = "payload.amount_usd >= 5000000"
SPEC = {
    "trigger": {"events": [{"signal": "funding.round", "where": WHERE}],
                "window": "30d"},
    "audience": {"sql": "select id as account_id from account"},
    "score": {"floor": 0},
    "route": {"tiers": [{"key": "t1"}]},
    "plays": {"t1": {}},
    "experiment": {"holdout_pct": 10, "unit": "account", "salt": "payloads"},
}


# -- the predicate itself -------------------------------------------------

def _signal(payload: dict) -> dict:
    return {"type": "funding.round", "payload": payload}


def test_an_absent_field_is_a_silent_non_match():
    """The documented case, and it stays silent: a source that has not sent a
    field yet is not doing anything wrong."""
    notes: list[triggers.Unanswerable] = []
    assert triggers.matches(SPEC, _signal({}), [_signal({})], notes=notes) is None
    assert notes == []


@pytest.mark.parametrize("value, kind", [("9000000", "a number sent as a string"),
                                         (None, "a null where a figure goes")])
def test_a_field_that_cannot_answer_is_a_non_match_and_is_reported(value, kind):
    """The case that used to raise. It is still a non-match — but somebody is
    told, because a source sending the wrong type looks exactly like a source
    sending nothing that matches, and the two need opposite responses."""
    payload = {"amount_usd": value}
    notes: list[triggers.Unanswerable] = []
    assert triggers.matches(SPEC, _signal(payload), [_signal(payload)], notes=notes) is None
    assert notes, f"{kind} was swallowed"
    assert notes[0].clause == WHERE
    assert "not supported between instances" in notes[0].detail


def test_a_caller_that_asks_for_no_notes_still_works():
    """`notes` is optional, and the predicate must not depend on being given
    one — the console and the tests call `matches` without it."""
    payload = {"amount_usd": "9000000"}
    assert triggers.matches(SPEC, _signal(payload), [_signal(payload)]) is None


# -- through the front door -----------------------------------------------

@pytest.fixture
def client(db):
    return TestClient(create_app(db), raise_server_exceptions=False)


@pytest.fixture
def armed(db, tenant):
    """A live program and an account the audience matches."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        row = programs.publish(cur, tid, key="payloads", version="1.0.0", spec=SPEC,
                               spec_hash=uuid.uuid4().hex, status="draft")
        programs.activate(cur, str(row["id"]))
        account = entities.upsert_account(cur, tid, name="Acme", domain="acme.test",
                                          country="ES")
        return {"tenant": tid, "account": str(account["id"]),
                "token": issue_api_key(db, tid, "test", ["ingest"]).token}


def _post(client, armed, payload):
    return client.post("/v1/signals", headers={"x-api-key": armed["token"]}, json={
        "entity_type": "account", "entity_id": armed["account"], "type": "funding.round",
        "strength": 0.9, "half_life_h": 720, "source": "test",
        "legal_basis": "legitimate_interest", "payload": payload,
        # Inside the trigger's 30-day window. A fixed date drifts out of it as
        # the calendar moves, and the test then proves nothing while passing
        # every assertion about warnings.
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "dedupe_key": uuid.uuid4().hex})


@requires_db
def test_a_quoted_number_is_answered_not_a_server_error(client, armed, fake):
    """The reproduction. This returned 500 — the outage the code's own comment
    says cannot happen, from the one input the runtime does not control."""
    answer = _post(client, armed, {"amount_usd": "9000000"})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["enrollments"] == []
    assert any(WHERE in warning for warning in body["warnings"]), body["warnings"]


@requires_db
def test_the_sender_is_told_which_clause_could_not_be_read(client, armed, fake):
    """A warning naming the program and the clause is a support answer. A 500
    is a mystery, and the sender's integration retries it for ever."""
    body = _post(client, armed, {"amount_usd": None}).json()
    assert body["warnings"]
    assert "payloads" in body["warnings"][0] and "could not evaluate" in body["warnings"][0]


@requires_db
def test_the_operator_can_see_which_source_is_sending_what(db, client, armed, fake):
    _post(client, armed, {"amount_usd": "9000000"})
    with db.tenant_tx(armed["tenant"]) as cur:
        cur.execute("select detail from audit_log where action = 'signal.unanswerable'")
        rows = [dict(r) for r in cur.fetchall()]
    assert rows, "an unreadable payload leaves no trace an operator can find"
    assert rows[0]["detail"]["signal"] == "funding.round"
    assert rows[0]["detail"]["clauses"][0]["where"] == WHERE


@requires_db
def test_a_correct_payload_still_enrols_and_says_nothing(client, armed, fake):
    """The guard has to leave the normal path alone."""
    body = _post(client, armed, {"amount_usd": 9_000_000}).json()
    assert body["enrollments"], "the fixture no longer enrols anybody"
    assert not [w for w in body["warnings"] if "could not evaluate" in w]


@requires_db
def test_an_absent_field_is_not_reported_to_the_sender(client, armed, fake):
    """The boundary between the two: absent is by design and stays quiet,
    unusable is a mistake somebody can fix and is named."""
    body = _post(client, armed, {}).json()
    assert body["enrollments"] == []
    assert not [w for w in body["warnings"] if "could not evaluate" in w]
