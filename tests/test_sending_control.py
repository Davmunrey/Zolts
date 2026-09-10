"""An operator can stop a burning domain, and the stop has a name on it.

`runtime/breakers.py` was the only writer of `sending_domain.paused` in this
repository. The only actor that could stop a send was a cut-off firing on rates
already earned, and `docs/09` calls that resource the one whose damage is not
recoverable on the timescale that matters. An operator who knew before the
numbers did — a list bought rather than built, a misaddressed campaign, a
partner on the phone — could watch and wait for a threshold to agree.

The reverse direction existed and was worse than absent: lifting a pause was a
flag on the command that *registers* a domain, so saying "the cause is fixed"
meant re-declaring SPF, DKIM, DMARC and unsubscribe in the same statement. That
failure does not raise. It arrives weeks later as mail filtered on delivery.

Three properties carry it, and each is tested behaviourally rather than by
reading a constant back.
"""

from __future__ import annotations

import uuid

import pytest

from runtime import sendingcontrol as control
from runtime.api import console as console_view
from tests.conftest import requires_db
from zolts.deliverability import Health, Metrics, assess
from zolts.sendingcontrol import (EMPTY_WORDS, MIN_REASON, Act, Refusal,
                                  Resumption, judge_resume, refuse_reason)


def _domain(cur, tenant_id, name="outbound.example", *, paused=False,
            reason=None):
    cur.execute(
        "insert into sending_domain (tenant_id, name, spf, dkim, dmarc_policy,"
        " one_click_unsubscribe, paused, paused_reason)"
        " values (%s,%s,true,true,'reject',true,%s,%s) returning *",
        (tenant_id, name, paused, reason))
    return dict(cur.fetchone())


def _sends(cur, tenant_id, *, domain="outbound.example", sent=0, complained=0,
           bounced=0, replied=0, paused_mailbox=False, address=None):
    """A mailbox on the domain with a measured history.

    Every timestamp is written rather than defaulted. A fixture that lets
    `now()` supply one end of a rate is a fixture that measures the clock.
    """
    box = address or f"box-{uuid.uuid4().hex[:8]}@{domain}"
    cur.execute(
        "insert into mailbox (tenant_id, address, domain, provider,"
        " warmup_started_on, paused) values (%s,%s,%s,'other',"
        " current_date - 60, %s) returning id",
        (tenant_id, box, domain, paused_mailbox))
    mailbox_id = cur.fetchone()["id"]
    for i in range(sent):
        status = ("bounced" if i < bounced
                  else "replied" if i < bounced + replied else "sent")
        cur.execute(
            "insert into touch (tenant_id, channel, idempotency_key, status,"
            " content, direction, mailbox_id, sent_at, complained_at)"
            " values (%s,'email',%s,%s,'{}','out',%s, now() - interval '1 day',"
            " %s)",
            (tenant_id, uuid.uuid4().hex, status, mailbox_id,
             None if i >= complained else "now()"))
    return mailbox_id


# -- what counts as a reason ---------------------------------------------


def test_nothing_written_is_refused_and_says_which_nothing():
    """Three refusals rather than one boolean: "give a reason", shown to
    somebody who gave one, reads as a broken form."""
    assert refuse_reason(None) is Refusal.NO_REASON
    assert refuse_reason("   ") is Refusal.NO_REASON
    assert refuse_reason("fixed") is Refusal.REASON_TOO_SHORT


def test_a_reason_that_restates_the_act_is_not_a_reason():
    """"Paused", "resume", "ok" pass a non-empty check and carry nothing.

    A field that accepts them teaches the operator the field is a formality,
    and a formality is what the next person types.
    """
    assert refuse_reason("ok ok ok okay ok") is Refusal.REASON_SAYS_NOTHING
    assert refuse_reason("resumed. fixed. done.") is Refusal.REASON_SAYS_NOTHING


def test_a_real_reason_containing_an_empty_word_is_accepted():
    """The comparison is per word against the whole reason, never a substring.

    "Fixed the bounce source in the export" is a reason that happens to start
    with a word on the list.
    """
    assert refuse_reason("fixed the bounce source in the export") is None
    assert refuse_reason("list was bought, not built") is None


def test_the_length_bound_is_behavioural_not_a_constant_read_back():
    """Built at the bound and required to flip there, because a constant
    compared against itself is this repository's dominant defect."""
    at_bound = "b" * MIN_REASON
    assert refuse_reason(at_bound) is None
    assert refuse_reason(at_bound[:-1]) is Refusal.REASON_TOO_SHORT


def test_every_empty_word_is_one_word():
    """A multi-word entry would never match, since the check splits first."""
    for word in EMPTY_WORDS:
        assert " " not in word, word


# -- what kind of act a resume is ----------------------------------------


def test_resuming_over_the_cut_off_is_named_as_the_lift_that_will_not_hold():
    """`assess` reads metrics and never reads the paused column.

    So its `PAUSED` is the strongest objection available — a rate at or over
    one of the two cut-offs in `docs/09` — and not a restatement of the
    domain's current state. Reading it the other way would have made the most
    dangerous resume in the product the one the console called ordinary.
    """
    verdict = assess(Metrics(sent=1000, complained=5))
    assert verdict.health is Health.PAUSED, "the premise: still over the cut-off"
    assert judge_resume(verdict) is Resumption.WILL_RETRIP


def test_resuming_over_an_alarm_but_under_the_cut_off_is_an_override():
    verdict = assess(Metrics(sent=1000, complained=2))
    assert verdict.health is Health.ALARM, "the premise: alarm, not cut-off"
    assert verdict.rule_key == "complaint.alarm"
    assert judge_resume(verdict) is Resumption.OVERRIDE


def test_a_clean_domain_resumes_cleanly():
    assert judge_resume(assess(Metrics(sent=1000, replied=30))) is Resumption.CLEAN


def test_an_ordinary_cold_outreach_reply_rate_is_not_an_override():
    """`reply.alarm` fires under 2%, and `zolts/deliverability` says in as many
    words that cold outreach lives just under 2% on a good day — which is why
    that rule alone does not halve a mailbox. Calling an ordinary reply rate an
    override would make the word mean nothing, which is the same failure as
    calling a live cut-off clean, pointed the other way."""
    verdict = assess(Metrics(sent=1000, replied=15))
    assert verdict.rule_key == "reply.alarm", "the premise: the advisory rule"
    assert verdict.health is Health.ALARM
    assert judge_resume(verdict) is Resumption.CLEAN


def test_a_collapsed_reply_rate_is_an_override():
    """Deliberately not advisory: engagement that low is a signal the providers
    themselves read, so resuming into it is resuming into a halved domain."""
    verdict = assess(Metrics(sent=1000, replied=0))
    assert verdict.rule_key == "reply.collapsed"
    assert judge_resume(verdict) is Resumption.OVERRIDE


# -- the acts, against a real database -----------------------------------


@requires_db
def test_an_operator_can_stop_a_domain_nothing_else_could_stop(db, tenant):
    """The gap this closes: `breakers.check` was the only writer."""
    with db.tenant_tx(tenant["id"]) as cur:
        _domain(cur, tenant["id"])
        outcome = control.pause(cur, tenant["id"], "outbound.example",
                                reason="list was bought, not built",
                                actor="key:someone")
        assert outcome.done and outcome.act is Act.PAUSE
        cur.execute("select paused, paused_reason from sending_domain"
                    " where name = 'outbound.example'")
        row = cur.fetchone()
    assert row["paused"] is True
    assert row["paused_reason"] == "list was bought, not built"


@requires_db
def test_the_stop_is_written_down_with_who_and_why(db, tenant):
    with db.tenant_tx(tenant["id"]) as cur:
        _domain(cur, tenant["id"])
        control.pause(cur, tenant["id"], "outbound.example",
                      reason="partner reported our mail in their spam folder",
                      actor="key:someone")
        cur.execute("select * from audit_log where action = 'sending.domain.paused'")
        entry = cur.fetchone()
    assert entry["actor"] == "key:someone"
    assert entry["subject"] == "outbound.example"
    assert entry["detail"]["reason"].startswith("partner reported")


@requires_db
def test_a_stop_with_no_reason_changes_nothing(db, tenant):
    """A refusal is never a silent no-op, and never a partial one."""
    with db.tenant_tx(tenant["id"]) as cur:
        _domain(cur, tenant["id"])
        outcome = control.pause(cur, tenant["id"], "outbound.example",
                                reason="  ", actor="key:someone")
        assert outcome.refusal is Refusal.NO_REASON
        cur.execute("select paused from sending_domain where name = 'outbound.example'")
        assert cur.fetchone()["paused"] is False
        cur.execute("select count(*) as n from audit_log")
        assert cur.fetchone()["n"] == 0, "a refused act wrote a log line"


@requires_db
def test_stopping_a_domain_that_does_not_exist_is_refused_not_ignored(db, tenant):
    """The worst of the three outcomes is the silent one: an operator who
    pressed Stop and was told nothing believes the sending stopped."""
    with db.tenant_tx(tenant["id"]) as cur:
        outcome = control.pause(cur, tenant["id"], "typo.example",
                                reason="stopping the wrong thing carefully",
                                actor="key:someone")
    assert outcome.refusal is Refusal.NOT_REGISTERED


@requires_db
def test_stopping_what_is_already_stopped_is_refused(db, tenant):
    with db.tenant_tx(tenant["id"]) as cur:
        _domain(cur, tenant["id"], paused=True, reason="complaint rate")
        outcome = control.pause(cur, tenant["id"], "outbound.example",
                                reason="stopping it a second time",
                                actor="key:someone")
        assert outcome.refusal is Refusal.ALREADY_PAUSED
        cur.execute("select paused_reason from sending_domain"
                    " where name = 'outbound.example'")
        assert cur.fetchone()["paused_reason"] == "complaint rate", (
            "the refused second stop overwrote the first one's reason")


@requires_db
def test_a_resume_records_that_it_overrode_a_live_cut_off(db, tenant):
    """The audit row says which kind of act it was, not merely that it happened.

    A log that records only that somebody lifted a cut-off is the log that
    gets read during the next incident by somebody who needs the missing half.
    """
    with db.tenant_tx(tenant["id"]) as cur:
        _domain(cur, tenant["id"], paused=True, reason="complaint.cutoff")
        _sends(cur, tenant["id"], sent=1000, complained=5)
        outcome = control.resume(cur, tenant["id"], "outbound.example",
                                 reason="provider confirmed a reporting error",
                                 actor="key:someone")
        cur.execute("select * from audit_log where action = 'sending.domain.resumed'")
        entry = cur.fetchone()
        cur.execute("select paused from sending_domain where name = 'outbound.example'")
        assert cur.fetchone()["paused"] is False
    assert outcome.resumption is Resumption.WILL_RETRIP
    assert entry["detail"]["resumption"] == "will_retrip"
    assert entry["detail"]["liftedReason"] == "complaint.cutoff"


@requires_db
def test_pausing_a_mailbox_does_not_launder_the_domains_history(db, tenant):
    """`fleet.load` drops a paused mailbox so its metrics do not dilute the
    fleet. Right for capacity, and wrong here: a domain that tripped the
    complaint cut-off would read as recovered the moment somebody paused the
    mailbox that did it, and the console would call the most dangerous resume
    in the product an ordinary one. The sends that burned the domain are the
    domain's sends."""
    with db.tenant_tx(tenant["id"]) as cur:
        _domain(cur, tenant["id"], paused=True, reason="complaint.cutoff")
        _sends(cur, tenant["id"], sent=1000, complained=5, paused_mailbox=True)
        outcome = control.resume(cur, tenant["id"], "outbound.example",
                                 reason="the mailbox that did it is paused",
                                 actor="key:someone")
    assert outcome.resumption is Resumption.WILL_RETRIP, (
        "pausing the mailbox hid the rate that tripped the cut-off")


@requires_db
def test_a_clean_resume_is_recorded_as_clean(db, tenant):
    with db.tenant_tx(tenant["id"]) as cur:
        _domain(cur, tenant["id"], paused=True, reason="complaint.cutoff")
        _sends(cur, tenant["id"], sent=1000, complained=0, replied=30)
        outcome = control.resume(cur, tenant["id"], "outbound.example",
                                 reason="the bought list has been removed",
                                 actor="key:someone")
    assert outcome.resumption is Resumption.CLEAN


@requires_db
def test_resuming_what_is_not_paused_is_refused(db, tenant):
    with db.tenant_tx(tenant["id"]) as cur:
        _domain(cur, tenant["id"])
        outcome = control.resume(cur, tenant["id"], "outbound.example",
                                 reason="lifting a pause that is not there",
                                 actor="key:someone")
    assert outcome.refusal is Refusal.ALREADY_SENDING


@requires_db
def test_a_resume_leaves_the_authentication_record_untouched(db, tenant):
    """The defect this module exists to remove.

    `zolts domain --resume` lifts the pause through an upsert of the domain's
    SPF, DKIM, DMARC and unsubscribe state, so the only way to say the cause is
    fixed was to restate four authentication facts correctly. Getting one wrong
    does not raise; it arrives weeks later as mail filtered on delivery.
    """
    with db.tenant_tx(tenant["id"]) as cur:
        before = _domain(cur, tenant["id"], paused=True, reason="complaint.cutoff")
        _sends(cur, tenant["id"], sent=1000, complained=0, replied=30)
        control.resume(cur, tenant["id"], "outbound.example",
                       reason="the bought list has been removed",
                       actor="key:someone")
        cur.execute("select * from sending_domain where name = 'outbound.example'")
        after = dict(cur.fetchone())
    for column in ("spf", "dkim", "dmarc_policy", "one_click_unsubscribe"):
        assert after[column] == before[column], column


# -- through the API, and onto the screen --------------------------------


@requires_db
def test_the_endpoint_stops_a_domain_and_refuses_an_empty_reason(db, tenant):
    """The whole path, because the rule has to hold at the endpoint too.

    A rule enforced in a module and skipped by its one caller is this
    repository's dominant defect, and the caller is what a browser reaches.
    """
    from fastapi.testclient import TestClient

    from runtime.api.app import create_app
    from runtime.provision import issue_api_key

    client = TestClient(create_app(db), raise_server_exceptions=False)
    token = issue_api_key(db, str(tenant["id"]), "test", []).token
    head = {"x-api-key": token}
    with db.tenant_tx(tenant["id"]) as cur:
        _domain(cur, tenant["id"])

    thin = client.post("/v1/sending/domains/outbound.example/pause",
                       headers=head, json={"reason": "ok"})
    assert thin.status_code == 422
    assert thin.json()["detail"]["refused"] == "reason_too_short"

    with db.tenant_tx(tenant["id"]) as cur:
        cur.execute("select paused from sending_domain where name = 'outbound.example'")
        assert cur.fetchone()["paused"] is False, "a refused call stopped the sending"

    done = client.post("/v1/sending/domains/outbound.example/pause",
                       headers=head, json={"reason": "list was bought, not built"})
    assert done.status_code == 200 and done.json()["paused"] is True

    again = client.post("/v1/sending/domains/outbound.example/pause",
                        headers=head, json={"reason": "stopping it a second time"})
    assert again.status_code == 409
    assert again.json()["detail"]["refused"] == "already_paused"


@requires_db
def test_the_screen_says_which_kind_of_resume_before_the_click(db, tenant):
    """`fleet.load` drops a paused domain, so the view could report that one was
    paused and nothing about whether the cause was still true. The operator
    could not tell, from the screen, the difference between agreeing with the
    measurement and overriding a live cut-off."""
    with db.tenant_tx(tenant["id"]) as cur:
        _domain(cur, tenant["id"], paused=True, reason="complaint.cutoff")
        _sends(cur, tenant["id"], sent=1000, complained=5)
        cur.execute("select * from tenant where id = %s", (tenant["id"],))
        model = console_view.build(cur, dict(cur.fetchone()))
    paused = model["fleet"]["pausedDomains"][0]
    assert paused["resumption"] == "will_retrip"
    assert "complaint" in paused["rule"]


def test_the_console_renders_the_switch_and_calls_the_endpoints():
    """A view model with no screen is the defect this repository keeps finding.

    Read out of the surface rather than rendered here: the browser check drives
    the real thing, types a reason, clicks Stop and reads the row back from the
    database. This holds the wiring that check depends on — a selectable domain
    row, a required reason, and both endpoints bound.
    """
    from pathlib import Path

    surface = (Path(__file__).resolve().parent.parent
               / "design" / "console.html").read_text(encoding="utf-8")
    assert 'data-d="' in surface, "no domain row can be selected"
    assert 'id="sc-reason"' in surface, "the reason field is not on the screen"
    assert '"/v1/sending/domains/" + encodeURIComponent(b.dataset.domain) + "/"' in surface
    assert "function switchBlock" in surface
    for refusal in ("no_reason", "reason_too_short", "reason_says_nothing",
                    "already_paused", "already_sending", "not_registered"):
        assert refusal + ":" in surface, (
            f"the screen cannot name the {refusal!r} refusal, so it would show "
            f"a generic failure and send the operator looking for a bug")
