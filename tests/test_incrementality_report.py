"""The incrementality report: the "after", frozen the way the "before" is.

`docs/10` says every program reports a real income statement and that a lift
is only ever reported beside the effect the sample can detect. `docs/17` says
the month-9 conversation compares after with before. The baseline was frozen
(ADR-042) and the "after" was recomputed on every request (D-46), so the
figure a partner read in month three was not one anybody could show in month
nine. The report is composed from what the runtime already records, frozen
at the close of a billing period, written once, and its verdict is one of
three words — none of which is "met" (ADR-043).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from runtime.api.app import create_app
from runtime.provision import issue_api_key
from runtime.repo import baseline as baseline_repo
from runtime.repo import reports as reports_repo
from tests.conftest import requires_db
from tests.test_baseline import DECLARED
from zolts.baseline import from_mapping as baseline_from_mapping
from zolts.report import (NOT_RESOLVABLE, NOT_SIGNIFICANT, SIGNIFICANT, VERDICTS,
                          BaselineQuote, Comparison, IncrementalityReport, ReportError,
                          from_mapping)


# -- the pure part --------------------------------------------------------

def _report(primary: Comparison, opportunities: Comparison | None = None, **overrides):
    fields = dict(
        program_key="flagship", program_version="2.1.0", spec_hash="abc",
        period_start=date(2026, 9, 3), period_end=date(2026, 10, 1), holdout_pct=10.0,
        primary=primary,
        opportunities=opportunities or Comparison(primary.treatment_enrolled,
                                                  primary.control_enrolled, 0, 0),
        converted_by_type={"reply_positive": {"treatment": primary.treatment_converted,
                                              "control": primary.control_converted}},
        unread_conversions=1, touches_sent=40, decisions={"allow": 40, "deny": 3},
        credits_by_kind={"email.send": 40.0, "program.step": 8.0},
        average_opportunity_micros=30_000_000_000, opportunities_with_amount=12,
        baseline=BaselineQuote(digest="f" * 64, window_start=date(2026, 6, 1),
                               window_end=date(2026, 8, 30),
                               monthly_spend_micros=11_300_000_000, meetings=9,
                               opportunities=3, cost_per_meeting_micros=3_766_666_666,
                               cost_per_opportunity_micros=11_300_000_000))
    fields.update(overrides)
    return IncrementalityReport(**fields)


def test_one_conversion_per_arm_is_not_resolvable_and_declares_nothing():
    """The floor from `zolts.experiment`, applied where the document is made."""
    r = _report(Comparison(200, 20, 1, 1))
    assert r.verdict == NOT_RESOLVABLE
    assert r.primary.minimum_detectable_effect is None
    assert r.primary.incremental_conversions is None
    assert r.incremental_pipeline_micros is None
    assert "fewer than 5 conversions" in r.primary.why_not_resolvable
    assert "not resolvable" in r.render_markdown()


def test_a_lift_under_the_detectable_effect_is_not_significant_and_never_met():
    r = _report(Comparison(200, 200, 12, 9))
    assert r.verdict == NOT_SIGNIFICANT
    assert r.primary.lift == pytest.approx(0.015)
    assert r.primary.minimum_detectable_effect > r.primary.lift
    rendered = r.render_markdown()
    words = {w.strip("*.,;:()").lower() for w in rendered.split()}
    assert "met" not in words and "success" not in words and "achieved" not in words, (
        "the report used a word that reads as a criterion met")
    assert "not significant" in rendered


def test_a_lift_over_the_detectable_effect_is_significant_and_counts_its_increment():
    r = _report(Comparison(1000, 1000, 90, 40), Comparison(1000, 1000, 30, 10))
    assert r.verdict == SIGNIFICANT
    assert r.primary.incremental_conversions == 50
    assert r.opportunities.verdict == SIGNIFICANT
    assert r.opportunities.incremental_conversions == 20
    assert r.incremental_pipeline_micros == 20 * 30_000_000_000
    assert r.pipeline_withheld_because is None
    assert "€600,000" in r.render_markdown()


def test_pipeline_is_never_computed_from_replies():
    """`docs/10`'s formula multiplies the lift by an opportunity value. A lift
    made of positive replies multiplied by a deal size is a number nobody can
    defend, so the pipeline rests on the opportunity comparison alone."""
    r = _report(Comparison(1000, 1000, 90, 40), Comparison(1000, 1000, 3, 1))
    assert r.verdict == SIGNIFICANT, "the primary comparison is significant"
    assert r.incremental_pipeline_micros is None
    assert "not resolvable" in r.pipeline_withheld_because
    assert "Not reported" in r.render_markdown()


def test_pipeline_is_withheld_without_a_deal_value_from_the_crm():
    r = _report(Comparison(1000, 1000, 90, 40), Comparison(1000, 1000, 30, 10),
                average_opportunity_micros=None, opportunities_with_amount=0)
    assert r.incremental_pipeline_micros is None
    assert "no opportunity with an amount" in r.pipeline_withheld_because


def test_every_verdict_is_one_of_three_words():
    for comparison in (Comparison(0, 0, 0, 0), Comparison(10, 0, 5, 0),
                       Comparison(200, 200, 12, 9), Comparison(1000, 1000, 90, 40)):
        assert comparison.verdict in VERDICTS
    assert Comparison(0, 0, 0, 0).why_not_resolvable == "nobody was enrolled"
    assert Comparison(10, 0, 5, 0).why_not_resolvable == "no control arm yet"
    assert Comparison(0, 0, 0, 0).treatment_rate is None, "a rate of nothing is not 0%"


def test_the_digest_covers_inputs_and_derived_figures_and_is_recomputable():
    """Either party can rebuild the report from its canonical JSON and get the
    same hash and the same document. That is what makes it signable."""
    original = _report(Comparison(1000, 1000, 90, 40), Comparison(1000, 1000, 30, 10))
    rebuilt = from_mapping(original.canonical())
    assert rebuilt.digest() == original.digest()
    assert rebuilt.render_markdown() == original.render_markdown()
    assert len(original.digest()) == 64
    assert original.canonical()["verdict"] == SIGNIFICANT
    assert original.canonical()["primary"]["incremental_conversions"] == 50

    for changed in (
            _report(Comparison(1000, 1000, 91, 40), Comparison(1000, 1000, 30, 10)),
            _report(Comparison(1000, 1000, 90, 40), Comparison(1000, 1000, 30, 10),
                    holdout_pct=15.0),
            _report(Comparison(1000, 1000, 90, 40), Comparison(1000, 1000, 30, 10),
                    credits_by_kind={"email.send": 41.0}),
            _report(Comparison(1000, 1000, 90, 40), Comparison(1000, 1000, 30, 10),
                    baseline=None)):
        assert changed.digest() != original.digest()


@pytest.mark.parametrize("broken, reason", [
    (dict(period_end=date(2026, 9, 1)), "ends before it starts"),
    (dict(holdout_pct=60), "holdout_pct"),
    (dict(unread_conversions=500), "more unread"),
])
def test_a_report_that_cannot_be_true_is_refused(broken, reason):
    with pytest.raises(ReportError, match=reason):
        _report(Comparison(200, 200, 12, 9), **broken)


def test_a_comparison_with_more_conversions_than_enrollments_is_refused():
    with pytest.raises(ReportError, match="more treatment conversions"):
        Comparison(10, 10, 11, 0)


def test_the_document_says_what_it_rests_on_and_what_it_cost():
    rendered = _report(Comparison(200, 200, 12, 9)).render_markdown()
    assert "40 touches sent" in rendered and "3 deny" in rendered
    assert "| email.send | 40 |" in rendered and "**48**" in rendered
    assert "resting on a reply nobody read: 1 (4.8%)" in rendered
    assert "Baseline `" + "f" * 64 + "`" in rendered
    assert "not against this window" in rendered, "the report must not compare periods"


def test_without_a_baseline_the_document_says_there_is_nothing_before():
    rendered = _report(Comparison(200, 200, 12, 9), baseline=None).render_markdown()
    assert "No baseline was frozen" in rendered and "decision 35" in rendered


# -- composed from what the runtime recorded --------------------------------

def _program(cur, tid: str, key: str = "p", holdout: int = 10) -> str:
    cur.execute(
        "insert into program (tenant_id, key, version, spec, spec_hash, status)"
        " values (%s,%s,'1.0.0',%s,'h','live') returning id",
        (tid, key, '{"experiment": {"holdout_pct": %d, "primary_metric": "m"}}' % holdout))
    return str(cur.fetchone()["id"])


def _arm(cur, tid: str, program_id: str, variant: str, n: int, *, converted: int = 0,
         unread: int = 0, opportunities: int = 0, entered_at=None) -> list[str]:
    ids = []
    for i in range(n):
        cur.execute(
            "insert into enrollment (tenant_id, program_id, entity_type, entity_id,"
            " variant, state, entered_at) values (%s,%s,'account',gen_random_uuid(),"
            " %s,'running',coalesce(%s, now())) returning id",
            (tid, program_id, variant, entered_at))
        enrollment_id = str(cur.fetchone()["id"])
        ids.append(enrollment_id)
        if i < converted:
            cur.execute(
                "insert into outcome (tenant_id, enrollment_id, type, occurred_at, source,"
                " verified_by) values (%s,%s,'reply_positive',now(),'t',%s)",
                (tid, enrollment_id, None if i < unread else "triage"))
        if i < opportunities:
            cur.execute(
                "insert into outcome (tenant_id, enrollment_id, type, occurred_at, source)"
                " values (%s,%s,'opp_created',now(),'t')", (tid, enrollment_id))
    return ids


def _touch_and_decide(cur, tid: str, program_id: str, enrollment_id: str,
                      decision: str = "allow") -> None:
    cur.execute(
        "insert into policy_decision (tenant_id, subject_type, subject_id, action, decision,"
        " rule_key, rationale) values (%s,'person',gen_random_uuid(),'email.send',%s,"
        " 'eu.b2b','within the rule') returning id", (tid, decision))
    decision_id = str(cur.fetchone()["id"])
    cur.execute(
        "insert into action (tenant_id, enrollment_id, program_id, kind, idempotency_key,"
        " state, policy_decision_id) values (%s,%s,%s,'dispatch',%s,%s,%s)",
        (tid, enrollment_id, program_id, f"k-{uuid.uuid4().hex}",
         "succeeded" if decision == "allow" else "cancelled", decision_id))
    if decision == "allow":
        cur.execute(
            "insert into touch (tenant_id, enrollment_id, channel, idempotency_key, status,"
            " sent_at) values (%s,%s,'email',%s,'sent',now())",
            (tid, enrollment_id, f"t-{uuid.uuid4().hex}"))


def _tenant_row(cur, tid: str) -> dict:
    cur.execute("select * from tenant where id = %s", (tid,))
    return dict(cur.fetchone())


def _close(cur, tid: str) -> dict:
    from runtime import metering

    row = _tenant_row(cur, tid)
    period = metering.open_period(cur, row)
    return metering.close_period(cur, row, str(period["id"]))


@requires_db
def test_the_report_is_composed_from_what_the_runtime_recorded(db, tenant):
    """Every figure comes from a table the operator already fills. Nothing is
    typed in for the report's sake (`docs/17`'s guardrail)."""
    from runtime import metering, reporting

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        row = _tenant_row(cur, tid)
        program_id = _program(cur, tid)
        treated = _arm(cur, tid, program_id, "treatment", 8, converted=6, unread=2,
                       opportunities=1)
        _arm(cur, tid, program_id, "control", 4, converted=1)
        for enrollment_id in treated[:5]:
            _touch_and_decide(cur, tid, program_id, enrollment_id)
        _touch_and_decide(cur, tid, program_id, treated[5], decision="deny")
        metering.meter(cur, row, kind="email.send", units=5, program_id=program_id)
        metering.meter(cur, row, kind="enrich.email", units=2, program_id=program_id)
        cur.execute("insert into opportunity (tenant_id, provider, crm_id, status,"
                    " amount_micros) values (%s,'hubspot','d1','open',20000000000),"
                    " (%s,'hubspot','d2','won',40000000000), (%s,'hubspot','d3','open',null)",
                    (tid, tid, tid))
        baseline_repo.freeze(cur, tid, baseline_from_mapping(DECLARED),
                             signed_by="R. Ortega", captured_by="operator")
        closed = _close(cur, tid)
        cur.execute("select * from program where id = %s", (program_id,))
        report = reporting.compose(cur, dict(cur.fetchone()), closed)

    assert report.primary.treatment_enrolled == 8 and report.primary.control_enrolled == 4
    assert report.primary.treatment_converted == 6 and report.primary.control_converted == 1
    assert report.converted_by_type == {"reply_positive": {"treatment": 6, "control": 1},
                                        "opp_created": {"treatment": 1}}
    assert report.opportunities.treatment_converted == 1
    assert report.unread_conversions == 2 and report.unread_share == pytest.approx(2 / 7)
    assert report.touches_sent == 5
    assert report.decisions == {"allow": 5, "deny": 1}
    assert report.credits_by_kind == {"email.send": 5.0, "enrich.email": 16.0}
    assert report.credits_total == 21.0
    assert report.average_opportunity_micros == 30_000_000_000
    assert report.opportunities_with_amount == 2, "a deal without an amount is not averaged"
    assert report.baseline.digest == baseline_from_mapping(DECLARED).digest()
    assert report.baseline.cost_per_meeting_micros == 33_900_000_000 // 9
    assert report.holdout_pct == 10.0
    assert report.period_start == date.today() or report.period_end > report.period_start
    assert report.verdict == NOT_RESOLVABLE, "one control conversion establishes no baseline"


@requires_db
def test_the_report_counts_only_what_happened_before_the_period_ended(db, tenant):
    """As of the close. An outcome dated after the period's end belongs to the
    next report; an enrollment entered after it is not in this one's arms."""
    from runtime import reporting

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        program_id = _program(cur, tid)
        closed = _close(cur, tid)
        after = closed["ends_at"] + timedelta(days=1)
        _arm(cur, tid, program_id, "treatment", 3, converted=1)
        _arm(cur, tid, program_id, "control", 2)
        late = _arm(cur, tid, program_id, "treatment", 2, entered_at=after)
        cur.execute("insert into outcome (tenant_id, enrollment_id, type, occurred_at, source)"
                    " values (%s,%s,'meeting',%s,'t')", (tid, late[0], after))
        cur.execute("select * from program where id = %s", (program_id,))
        report = reporting.compose(cur, dict(cur.fetchone()), closed)

    assert report.primary.treatment_enrolled == 3
    assert report.primary.treatment_converted == 1
    assert "meeting" not in report.converted_by_type


@requires_db
def test_another_programs_enrollments_are_not_this_reports(db, tenant):
    from runtime import reporting

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        mine = _program(cur, tid, key="mine")
        other = _program(cur, tid, key="other")
        _arm(cur, tid, other, "treatment", 50, converted=20)
        _arm(cur, tid, other, "control", 50, converted=5)
        _arm(cur, tid, mine, "treatment", 1)
        closed = _close(cur, tid)
        cur.execute("select * from program where id = %s", (mine,))
        report = reporting.compose(cur, dict(cur.fetchone()), closed)
    assert report.enrolled == 1 and report.conversions == 0


# -- frozen at close, once ---------------------------------------------------

@requires_db
def test_the_close_freezes_one_report_per_program_and_only_once(db, tenant):
    from runtime import reporting

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        a = _program(cur, tid, key="a")
        b = _program(cur, tid, key="b")
        _program(cur, tid, key="never-enrolled")
        _arm(cur, tid, a, "treatment", 3)
        _arm(cur, tid, b, "control", 2)
        closed = _close(cur, tid)
        row = _tenant_row(cur, tid)
        frozen = reporting.freeze_all(cur, row, closed, frozen_by="operator")
        assert {str(r["program_id"]) for r in frozen} == {a, b}, (
            "a program that enrolled nobody has nothing to report")

        # More data arrives; the close is repeated. The rows do not move.
        _arm(cur, tid, a, "treatment", 30, converted=20)
        again = reporting.freeze_all(cur, row, closed, frozen_by="operator")
        assert [r["id"] for r in again] == [r["id"] for r in frozen]
        assert again[0]["digest"] == frozen[0]["digest"]

        cur.execute("select * from program where id = %s", (a,))
        with pytest.raises(reports_repo.ReportAlreadyFrozen):
            reporting.freeze(cur, tid, dict(cur.fetchone()), closed, frozen_by="operator")

        cur.execute("select detail from audit_log where action = 'report.frozen'")
        audited = cur.fetchall()
    assert len(audited) == 2
    assert {d["detail"]["digest"] for d in audited} == {r["digest"] for r in frozen}
    assert all(r["verdict"] == NOT_RESOLVABLE for r in frozen)


@requires_db
def test_a_report_is_not_frozen_while_the_period_is_open(db, tenant):
    """The period's costs are still being written. A report frozen mid-period
    is the test stopped on a favourable read (`docs/10`)."""
    from runtime import metering, reporting

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        program_id = _program(cur, tid)
        _arm(cur, tid, program_id, "treatment", 1)
        period = metering.open_period(cur, _tenant_row(cur, tid))
        cur.execute("select * from program where id = %s", (program_id,))
        with pytest.raises(ReportError, match="open"):
            reporting.freeze(cur, tid, dict(cur.fetchone()), period, frozen_by="operator")


@requires_db
def test_the_stored_document_is_the_one_anybody_can_rebuild_from_the_body(db, tenant):
    from runtime import reporting

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        program_id = _program(cur, tid)
        _arm(cur, tid, program_id, "treatment", 6, converted=5)
        _arm(cur, tid, program_id, "control", 6, converted=5)
        closed = _close(cur, tid)
        [frozen] = reporting.freeze_all(cur, _tenant_row(cur, tid), closed, frozen_by="x")
    rebuilt = from_mapping(frozen["body"])
    assert rebuilt.digest() == frozen["digest"]
    assert rebuilt.render_markdown() == frozen["rendered"]
    assert frozen["digest"] in frozen["rendered"]


@requires_db
def test_the_serving_role_cannot_restate_a_frozen_report_or_a_baseline(db, tenant):
    """Write-once by construction. The role that serves requests has no
    statement that can change either document a partner signs against."""
    import psycopg

    with db.pool.connection() as conn:
        for statement in ("update incrementality_report set verdict = 'significant'",
                          "delete from incrementality_report",
                          "update tenant_baseline set meetings = 90",
                          "delete from tenant_baseline"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)
            conn.rollback()


@requires_db
def test_a_report_is_the_tenants_own(db, tenant, other_tenant):
    from runtime import reporting

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        program_id = _program(cur, tid)
        _arm(cur, tid, program_id, "treatment", 1)
        closed = _close(cur, tid)
        [frozen] = reporting.freeze_all(cur, _tenant_row(cur, tid), closed, frozen_by="x")
    with db.tenant_tx(str(other_tenant["id"])) as cur:
        assert reports_repo.get(cur, str(frozen["id"])) is None
        assert reports_repo.for_program(cur, program_id) == []


# -- read back: the API and the command line --------------------------------

@pytest.fixture
def client(db):
    return TestClient(create_app(db), raise_server_exceptions=False)


@pytest.fixture
def key(db, tenant):
    return issue_api_key(db, str(tenant["id"]), "test", []).token


def _auth(token: str) -> dict[str, str]:
    return {"x-api-key": token}


@requires_db
def test_the_api_reads_the_frozen_reports_and_never_writes_one(db, client, key, tenant):
    from runtime import reporting

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        program_id = _program(cur, tid)
        _arm(cur, tid, program_id, "treatment", 2)
        closed = _close(cur, tid)
        [frozen] = reporting.freeze_all(cur, _tenant_row(cur, tid), closed, frozen_by="x")

    listed = client.get(f"/v1/programs/{program_id}/reports", headers=_auth(key))
    assert listed.status_code == 200, listed.text
    [report] = listed.json()
    assert report["id"] == str(frozen["id"]) and report["digest"] == frozen["digest"]
    assert report["verdict"] == NOT_RESOLVABLE and "tenant_id" not in report

    one = client.get(f"/v1/reports/{frozen['id']}", headers=_auth(key))
    assert one.status_code == 200 and one.json()["body"]["verdict"] == NOT_RESOLVABLE

    text = client.get(f"/v1/reports/{frozen['id']}?format=markdown", headers=_auth(key))
    assert text.status_code == 200
    assert text.headers["content-type"].startswith("text/markdown")
    assert text.text == frozen["rendered"]

    assert client.post(f"/v1/programs/{program_id}/reports",
                       headers=_auth(key)).status_code == 405, (
        "a report is frozen by the close, never on request")
    assert client.get(f"/v1/programs/{uuid.uuid4()}/reports",
                      headers=_auth(key)).status_code == 404


@requires_db
def test_another_tenant_cannot_read_a_report_by_its_id(db, client, tenant, other_tenant):
    from runtime import reporting

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        program_id = _program(cur, tid)
        _arm(cur, tid, program_id, "treatment", 1)
        closed = _close(cur, tid)
        [frozen] = reporting.freeze_all(cur, _tenant_row(cur, tid), closed, frozen_by="x")
    stranger = issue_api_key(db, str(other_tenant["id"]), "other", []).token
    assert client.get(f"/v1/reports/{frozen['id']}", headers=_auth(stranger)).status_code == 404
    assert client.get(f"/v1/programs/{program_id}/reports",
                      headers=_auth(stranger)).status_code == 404


@requires_db
def test_closing_from_the_command_line_freezes_and_prints_the_reports(
        db, tenant, monkeypatch, capsys):
    import json

    from runtime import cli
    from tests.conftest import APP_URL, OWNER_URL, SECRET

    monkeypatch.setenv("ZOLTS_DATABASE_URL", OWNER_URL)
    monkeypatch.setenv("ZOLTS_APP_DATABASE_URL", APP_URL)
    monkeypatch.setenv("ZOLTS_SECRET_KEY", SECRET)
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        program_id = _program(cur, tid, key="flagship")
        _arm(cur, tid, program_id, "treatment", 2)

    assert cli.main(["report", "--tenant", tid]) == 1, "nothing is frozen before a close"
    assert "frozen by close-period" in capsys.readouterr().err

    assert cli.main(["close-period", "--tenant", tid]) == 0
    closed = json.loads(capsys.readouterr().out)
    assert closed["reports"] == [{"program": program_id, "verdict": NOT_RESOLVABLE,
                                  "digest": closed["reports"][0]["digest"]}]

    assert cli.main(["report", "--tenant", tid, "--program", "flagship"]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("# Incrementality report — flagship v1.0.0")
    assert closed["reports"][0]["digest"] in printed

    assert cli.main(["report", "--tenant", tid, "--json"]) == 0
    [body] = json.loads(capsys.readouterr().out)
    assert body["digest"] == closed["reports"][0]["digest"]
    assert from_mapping(body["body"]).digest() == body["digest"]
