"""Why this person: one timeline per contact, from rows that already existed.

Six tables held a contact's story. A signal on the person or on their account.
An enrolment. A policy decision with the rule that made it and the digest of
the pack that held the rule. A proposal with the evidence behind every sentence
and the claims removed for having none. A touch with its provider and cost. An
outcome. Each was rendered on its own screen, and no screen read one contact
across all six — the register's dominant shape, applied to the one question a
customer's DPO and a customer's CRO ask in the same words: *why did this person
get this?* (`docs/28`, OX-1; the first half of `docs/11` COMP-2.)

Three properties carry it, each tested behaviourally.

**Account events belong to the person.** The worker resolves an account
enrolment to a contact through `membership`; a timeline that left account rows
out would show a contact who received three emails and nothing that explains
one.

**Newest first, and within one instant, effect above cause.** A decision and
the touch it allowed can share a second. Read top-down the touch is what
happened and the decision is why.

**A kind the rule does not know is refused, never rendered blank.** A row the
reader cannot name is the row they skip, and the row they skip is the one the
request was about.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from runtime import timeline as reader
from tests.conftest import requires_db
from zolts.timeline import ORDER, BY_KEY, kind, order

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
SURFACE = None


def _surface():
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent
            / "design" / "console.html").read_text(encoding="utf-8")


# -- the rule ------------------------------------------------------------


def test_every_kind_says_what_a_reader_may_conclude():
    for rule in ORDER:
        assert rule.label, rule.key
        assert len(rule.proves) > 40 and rule.proves.strip().endswith("."), (
            f"{rule.key} does not say what it proves")


def test_effect_sits_above_cause_within_one_instant():
    """The decision allowed the touch; top-down, the touch reads first."""
    at = NOW.isoformat()
    shown = order([{"kind": "decision.allow", "at": at},
                   {"kind": "touch.sent", "at": at},
                   {"kind": "signal.observed", "at": at}])
    assert [e["kind"] for e in shown] == [
        "touch.sent", "decision.allow", "signal.observed"]


def test_newest_first_across_instants():
    early = (NOW - timedelta(days=1)).isoformat()
    shown = order([{"kind": "signal.observed", "at": early},
                   {"kind": "touch.sent", "at": NOW.isoformat()}])
    assert [e["kind"] for e in shown] == ["touch.sent", "signal.observed"]


def test_a_kind_nobody_named_is_refused_not_rendered_blank():
    with pytest.raises(KeyError, match="not a kind of event"):
        kind("something.else")
    with pytest.raises(KeyError):
        order([{"kind": "something.else", "at": NOW.isoformat()}])


def test_the_surface_labels_every_kind_the_rule_knows():
    """A kind the runtime emits and the screen cannot name shows as its raw
    key, which is a row the reader skips."""
    surface = _surface()
    for rule in ORDER:
        assert f'"{rule.key}":' in surface, (
            f"the console has no label for {rule.key}")


# -- against a real database ---------------------------------------------


def _person(cur, tenant_id, name="Dana Reyes"):
    cur.execute("insert into person (tenant_id, full_name, country)"
                " values (%s,%s,'ES') returning id", (tenant_id, name))
    return str(cur.fetchone()["id"])


def _program(cur, tenant_id, key="starter"):
    cur.execute("insert into program (tenant_id, key, version, spec, spec_hash,"
                " status) values (%s,%s,'1.0.0','{}','h','live') returning id",
                (tenant_id, key))
    return str(cur.fetchone()["id"])


def _enrol(cur, tenant_id, program_id, entity_type, entity_id, at):
    cur.execute("insert into enrollment (tenant_id, program_id, entity_type,"
                " entity_id, variant, tier, state, entered_at)"
                " values (%s,%s,%s,%s,'treatment','A','active',%s) returning id",
                (tenant_id, program_id, entity_type, entity_id, at))
    return str(cur.fetchone()["id"])


@requires_db
def test_a_gated_send_reads_back_with_rule_digest_and_dropped_claim(db, tenant):
    """The exit criterion of OX-1, at the reader rather than the screen."""
    with db.tenant_tx(tenant["id"]) as cur:
        person = _person(cur, tenant["id"])
        program = _program(cur, tenant["id"])
        enrol = _enrol(cur, tenant["id"], program, "person", person,
                       NOW - timedelta(days=2))
        cur.execute(
            "insert into policy_decision (tenant_id, subject_type, subject_id,"
            " action, decision, rule_key, jurisdiction, rationale, pack_version,"
            " pack_digest, decided_at) values (%s,'person',%s,'email.send',"
            " 'allow','b2b.legitimate_interest','ES','outside quiet hours',"
            " '1.0.0','f00dfeedcafe',%s)", (tenant["id"], person, NOW - timedelta(days=1)))
        cur.execute(
            "insert into proposal (tenant_id, enrollment_id, program_id, agent,"
            " step_key, channel, idempotency_key, model, prompt_version, content,"
            " evidence, eval, eval_score, spend, cost_micros, state, created_at)"
            " values (%s,%s,%s,'copywriter','email_1','email',%s,'m','v1',%s,%s,"
            " '{}',0.9,'{}',100,'approved',%s)",
            (tenant["id"], enrol, program, uuid.uuid4().hex,
             json.dumps({"body": "x", "dropped_claims": ["They plan a Lisbon office."]}),
             json.dumps([{"ref": "e1", "source": "press", "text": "raised"}]),
             NOW - timedelta(days=1)))
        cur.execute(
            "insert into touch (tenant_id, enrollment_id, person_id, channel,"
            " direction, step_key, idempotency_key, status, content, provider,"
            " cost_micros, sent_at) values (%s,%s,%s,'email','out','email_1',%s,"
            " 'sent','{}','smartlead',1200,%s)",
            (tenant["id"], enrol, person, uuid.uuid4().hex, NOW - timedelta(hours=20)))
        story = reader.for_person(cur, person)

    kinds = [e["kind"] for e in story["events"]]
    assert kinds == ["touch.sent", "proposal.drafted", "decision.allow",
                     "enrollment.entered"], kinds
    decision = [e for e in story["events"] if e["kind"] == "decision.allow"][0]
    assert decision["rule"] == "b2b.legitimate_interest"
    assert decision["packDigest"] == "f00dfeedcafe"
    proposal = [e for e in story["events"] if e["kind"] == "proposal.drafted"][0]
    assert proposal["droppedClaims"] == ["They plan a Lisbon office."]
    assert proposal["evidence"][0]["ref"] == "e1"
    sent = [e for e in story["events"] if e["kind"] == "touch.sent"][0]
    assert sent["provider"] == "smartlead" and sent["costEur"] == 0.0012


@requires_db
def test_an_accounts_signal_and_enrolment_belong_to_its_members(db, tenant):
    """The worker reached the person through membership; so does the story."""
    with db.tenant_tx(tenant["id"]) as cur:
        person = _person(cur, tenant["id"])
        cur.execute("insert into account (tenant_id, name, domain)"
                    " values (%s,'Northbeam','northbeam.example') returning id",
                    (tenant["id"],))
        account = str(cur.fetchone()["id"])
        cur.execute("insert into membership (tenant_id, person_id, account_id,"
                    " started_at) values (%s,%s,%s,current_date)",
                    (tenant["id"], person, account))
        cur.execute(
            "insert into signal (tenant_id, entity_type, entity_id, type,"
            " strength, half_life_h, source, legal_basis, dedupe_key,"
            " observed_at, ingested_at) values (%s,'account',%s,'funding.round',"
            " 1,48,'press','legitimate_interest',%s,%s,%s)",
            (tenant["id"], account, uuid.uuid4().hex, NOW - timedelta(days=3),
             NOW - timedelta(days=3)))
        program = _program(cur, tenant["id"])
        _enrol(cur, tenant["id"], program, "account", account, NOW - timedelta(days=2))
        story = reader.for_person(cur, person)
    kinds = [e["kind"] for e in story["events"]]
    assert kinds == ["enrollment.entered", "signal.observed"], kinds
    assert story["events"][1]["detail"] == "on Northbeam"


@requires_db
def test_a_touch_written_before_the_person_column_is_still_found(db, tenant):
    """`touch.person_id` exists since ADR-056. The story does not start on
    the day the column did."""
    with db.tenant_tx(tenant["id"]) as cur:
        person = _person(cur, tenant["id"])
        program = _program(cur, tenant["id"])
        enrol = _enrol(cur, tenant["id"], program, "person", person, NOW - timedelta(days=2))
        cur.execute(
            "insert into touch (tenant_id, enrollment_id, channel, direction,"
            " step_key, idempotency_key, status, content, sent_at)"
            " values (%s,%s,'email','out','email_1',%s,'sent','{}',%s)",
            (tenant["id"], enrol, uuid.uuid4().hex, NOW - timedelta(days=1)))
        story = reader.for_person(cur, person)
    assert [e["kind"] for e in story["events"]] == ["touch.sent", "enrollment.entered"]


@requires_db
def test_nobody_is_a_clean_empty_story_not_an_error(db, tenant):
    with db.tenant_tx(tenant["id"]) as cur:
        person = _person(cur, tenant["id"])
        story = reader.for_person(cur, person)
    assert story["events"] == [] and story["counts"] == {}
    assert story["person"]["name"] == "Dana Reyes"


@requires_db
def test_another_tenants_person_reads_as_no_person(db, tenant, other_tenant):
    """One 404 for both: a difference is a way to enumerate a neighbour's book."""
    from fastapi.testclient import TestClient

    from runtime.api.app import create_app
    from runtime.provision import issue_api_key

    with db.tenant_tx(other_tenant["id"]) as cur:
        theirs = _person(cur, other_tenant["id"], "Someone Else")
    client = TestClient(create_app(db), raise_server_exceptions=False)
    token = issue_api_key(db, str(tenant["id"]), "test", []).token
    head = {"x-api-key": token}
    assert client.get(f"/v1/people/{theirs}/timeline", headers=head).status_code == 404
    assert client.get(f"/v1/people/{uuid.uuid4()}/timeline", headers=head).status_code == 404


def test_the_console_reads_the_timeline_on_demand():
    surface = _surface()
    assert 'read("/v1/people/" + encodeURIComponent(id) + "/timeline")' in surface
    assert 'data-c="' in surface, "no contact row can be selected"
    assert "function timelineBlock" in surface and 'id="tl"' in surface
    assert "removed: nothing in the evidence supported it" in surface


@requires_db
def test_the_subject_request_is_the_timeline_plus_the_record(db, tenant):
    """The access half of COMP-2, built on the timeline so a DPO's export and
    an operator's screen cannot disagree about what happened."""
    with db.tenant_tx(tenant["id"]) as cur:
        person = _person(cur, tenant["id"])
        cur.execute("update person set consent_state = %s where id = %s",
                    (json.dumps({"email": {"opted_out": False}}), person))
        cur.execute("insert into account (tenant_id, name, domain)"
                    " values (%s,'Northbeam','northbeam.example') returning id",
                    (tenant["id"],))
        account = str(cur.fetchone()["id"])
        cur.execute("insert into membership (tenant_id, person_id, account_id,"
                    " title, started_at) values (%s,%s,%s,'CRO',current_date)",
                    (tenant["id"], person, account))
        program = _program(cur, tenant["id"])
        _enrol(cur, tenant["id"], program, "person", person, NOW - timedelta(days=1))
        export = reader.subject_request(cur, person)
        story = reader.for_person(cur, person)
    assert export["kind"] == "subject_request"
    assert "erasure is not included" in export["scope"]
    assert export["person"]["consentState"] == {"email": {"opted_out": False}}
    assert export["memberships"][0]["name"] == "Northbeam"
    assert export["memberships"][0]["title"] == "CRO"
    assert export["timeline"] == story["events"], (
        "the export and the screen disagree about what happened")


@requires_db
def test_the_subject_request_answers_over_http(db, tenant):
    from fastapi.testclient import TestClient

    from runtime.api.app import create_app
    from runtime.provision import issue_api_key

    with db.tenant_tx(tenant["id"]) as cur:
        person = _person(cur, tenant["id"])
    client = TestClient(create_app(db), raise_server_exceptions=False)
    token = issue_api_key(db, str(tenant["id"]), "test", []).token
    answer = client.get(f"/v1/people/{person}/subject-request",
                        headers={"x-api-key": token})
    assert answer.status_code == 200, answer.text
    assert answer.json()["person"]["fullName"] == "Dana Reyes"
