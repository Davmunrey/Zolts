"""The audience decides who a program may enrol, and until now nothing read it.

`spec.audience` is required by the schema and validated on every publish.
Enrolment matched on the signal type, the trigger window, the trigger
predicate, the cooldown and the holdout, and never on membership — so a
program whose audience excluded accounts with an open opportunity enrolled
them anyway, and every exclusion a customer wrote was decoration.

The first test below is the one that would have failed before this existed.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from runtime.engine import audience, enroll
from tests.conftest import requires_db

SOFTWARE_ONLY = (
    "select a.id as account_id from account a where a.industry_code = 'software'")


def _spec(sql: str) -> dict:
    """A live program reduced to what enrolment reads."""
    return {
        "trigger": {"events": [{"signal": "funding.round"}], "window": "30d"},
        "audience": {"sql": sql},
        "score": {"floor": 0},
        "route": {"tiers": [{"key": "t1"}]},
        "plays": {"t1": {}},
        "experiment": {"holdout_pct": 10, "unit": "account",
                       "primary_metric": "signed_contract_60d"},
    }


# -- the static contract --------------------------------------------------

def test_an_audience_that_selects_no_subject_cannot_be_evaluated():
    """The runtime matches an entity against a column. A query exposing
    neither `account_id` nor `person_id` gives it nothing to match."""
    with pytest.raises(audience.AudienceError, match="selects neither"):
        audience.check(_spec("select 1 from account"), "demo")


def test_an_audience_that_writes_is_refused():
    """Row level security is the privilege boundary and holds regardless. This
    catches the mistake: an audience that writes would be run once per signal
    by the worker, and found long after the publish that caused it."""
    with pytest.raises(audience.AudienceError, match="modifies data"):
        audience.check(_spec("delete from account returning id as account_id"), "demo")


def test_a_segment_reference_is_refused_because_segments_do_not_exist():
    """The schema allows `segment_ref` and nothing resolves one. A program
    pointing at a segment that does not exist would enrol nobody and say
    nothing about why. Decision 31."""
    spec = _spec(SOFTWARE_ONLY)
    spec["audience"] = {"segment_ref": "enterprise-eu"}
    with pytest.raises(audience.AudienceError, match="segments are not implemented"):
        audience.check(spec, "demo")


def test_a_missing_audience_is_refused():
    spec = _spec(SOFTWARE_ONLY)
    del spec["audience"]
    with pytest.raises(audience.AudienceError, match="no audience"):
        audience.check(spec, "demo")


def test_the_subject_column_is_read_from_the_query():
    assert audience.subject_column("select p.id as person_id from person p") == "person_id"
    assert audience.subject_column(SOFTWARE_ONLY) == "account_id"


# -- against a real database ----------------------------------------------

@requires_db
def test_an_account_outside_the_audience_is_not_enrolled(db, tenant):
    """The defect, stated as a test.

    Two accounts, one software and one not. Both emit the trigger. Before the
    audience was executed both were enrolled; the program said it was for
    software companies and contacted everybody.
    """
    from runtime.repo import entities

    with db.tenant_tx(tenant["id"]) as cur:
        inside = entities.upsert_account(cur, tenant["id"], name="In Scope",
                                         industry_code="software")
        outside = entities.upsert_account(cur, tenant["id"], name="Out Of Scope",
                                          industry_code="construction")

        assert audience.includes(cur, _spec(SOFTWARE_ONLY), "demo", str(inside["id"]))
        assert not audience.includes(cur, _spec(SOFTWARE_ONLY), "demo", str(outside["id"]))


@requires_db
def test_an_audience_that_excludes_a_suppressed_account_excludes_it(db, tenant):
    """The exclusion every shipped audience carries, now actually applied.

    The policy gate suppresses at send time and always did, so nobody was
    contacted. But an enrolment is not free: it spends enrichment, it occupies
    a holdout arm, and it puts a suppressed account into a program's reported
    denominator. The audience is where that account should never have entered.
    """
    from runtime.repo import entities

    sql = ("select a.id as account_id from account a"
           " where not exists (select 1 from suppression s"
           "                    where s.scope = 'account' and s.value = a.id::text)")
    with db.tenant_tx(tenant["id"]) as cur:
        allowed = entities.upsert_account(cur, tenant["id"], name="Contactable")
        blocked = entities.upsert_account(cur, tenant["id"], name="Suppressed")
        cur.execute("insert into suppression (tenant_id, scope, value, reason, source)"
                    " values (%s,'account',%s,'asked us to stop','test')",
                    (tenant["id"], str(blocked["id"])))

        assert audience.includes(cur, _spec(sql), "demo", str(allowed["id"]))
        assert not audience.includes(cur, _spec(sql), "demo", str(blocked["id"]))


@requires_db
def test_an_audience_naming_a_table_this_tenant_does_not_have_fails_closed(db, tenant):
    """A broken audience stops the enrolment rather than allowing it. The
    alternative is what shipped: contacting people the program promised not
    to contact."""
    from runtime.repo import entities

    sql = "select w.account_id from a_table_nobody_has w"
    with db.tenant_tx(tenant["id"]) as cur:
        account = entities.upsert_account(cur, tenant["id"], name="Anyone")
        with pytest.raises(audience.AudienceError, match="failed"):
            audience.includes(cur, _spec(sql), "demo", str(account["id"]))


@requires_db
def test_the_audience_cannot_see_another_tenants_rows(db, tenant, other_tenant):
    """Row level security is the boundary, and it applies to tenant-authored
    SQL exactly as it applies to the runtime's own."""
    from runtime.repo import entities

    with db.tenant_tx(other_tenant["id"]) as cur:
        theirs = entities.upsert_account(cur, other_tenant["id"], name="Theirs",
                                         industry_code="software")

    with db.tenant_tx(tenant["id"]) as cur:
        assert not audience.includes(cur, _spec(SOFTWARE_ONLY), "demo", str(theirs["id"])), (
            "an audience reached another tenant's account")


@requires_db
def test_ingest_does_not_enrol_a_subject_outside_the_audience(db, tenant):
    """End to end: the signal fires, the trigger matches, and the account is
    still not enrolled because the program is not for it."""
    from runtime.repo import entities, programs

    with db.tenant_tx(tenant["id"]) as cur:
        outside = entities.upsert_account(cur, tenant["id"], name="Wrong Segment",
                                          industry_code="construction")
        published = programs.publish(
            cur, tenant["id"], key="audience-demo", version="1.0.0",
            spec=_spec(SOFTWARE_ONLY), spec_hash="audience-demo-hash",
            metadata={"name": "Audience demo"})
        programs.activate(cur, str(published["id"]))

        result = enroll.ingest(
            cur, tenant["id"], entity_type="account", entity_id=str(outside["id"]),
            type="funding.round", strength=0.9, half_life_h=72, source="test",
            legal_basis="legitimate_interest", payload={},
            observed_at=datetime.now(timezone.utc))

        enrolled = [e for e in result.enrollments if e.program_key == "audience-demo"]
        assert not enrolled, "an account outside the audience was enrolled"


@requires_db
def test_ingest_enrols_a_subject_inside_the_audience(db, tenant):
    """The other half. A membership test that refuses everybody would pass the
    test above and break the product."""
    from runtime.repo import entities, programs

    with db.tenant_tx(tenant["id"]) as cur:
        inside = entities.upsert_account(cur, tenant["id"], name="Right Segment",
                                         industry_code="software")
        published = programs.publish(
            cur, tenant["id"], key="audience-demo-in", version="1.0.0",
            spec=_spec(SOFTWARE_ONLY), spec_hash="audience-demo-in-hash",
            metadata={"name": "Audience demo in"})
        programs.activate(cur, str(published["id"]))

        result = enroll.ingest(
            cur, tenant["id"], entity_type="account", entity_id=str(inside["id"]),
            type="funding.round", strength=0.9, half_life_h=72, source="test",
            legal_basis="legitimate_interest", payload={},
            observed_at=datetime.now(timezone.utc))

        assert [e for e in result.enrollments if e.program_key == "audience-demo-in"], (
            "an account inside the audience was not enrolled")


@requires_db
def test_a_broken_audience_records_why_it_refused(db, tenant):
    """A program that has stopped enrolling because its audience is broken
    looks exactly like a program with no matching signals. The difference is
    worth an audit row."""
    from runtime.repo import entities, programs

    with db.tenant_tx(tenant["id"]) as cur:
        account = entities.upsert_account(cur, tenant["id"], name="Anyone")
        published = programs.publish(
            cur, tenant["id"], key="audience-broken", version="1.0.0",
            spec=_spec("select w.account_id from a_table_nobody_has w"),
            spec_hash="audience-broken-hash", metadata={"name": "Broken"})
        programs.activate(cur, str(published["id"]))

        enroll.ingest(
            cur, tenant["id"], entity_type="account", entity_id=str(account["id"]),
            type="funding.round", strength=0.9, half_life_h=72, source="test",
            legal_basis="legitimate_interest", payload={},
            observed_at=datetime.now(timezone.utc))

        cur.execute("select detail from audit_log where action = 'enrollment.refused'"
                    " order by at desc limit 1")
        row = cur.fetchone()
        assert row is not None, "a refusal was not recorded"
        assert "a_table_nobody_has" in row["detail"]["reason"]


# -- the shipped programs -------------------------------------------------

@requires_db
def test_every_shipped_programs_audience_runs_against_the_real_schema(db, tenant):
    """The guard that would have caught all of this on the first commit.

    An audience is validated against the JSON Schema, which checks that the
    document has the right shape. Nothing checked that the query it contains
    can execute, so the shipped programs referred to a column and two tables
    that the runtime's schema does not have — invisible for as long as nothing
    ran them, and fatal the moment something did.
    """
    from pathlib import Path

    from zolts import dsl

    programs = sorted(Path("examples/programs").glob("*.yaml"))
    assert programs, "no example programs to check"

    broken = []
    with db.tenant_tx(tenant["id"]) as cur:
        # An audience that excludes accounts with an open deal refuses to run
        # until a CRM has actually delivered deals, because an empty table
        # otherwise reads as "nobody is in a deal" (ADR-038). That refusal is
        # the subject of `test_opportunity.py`; what this test asks is whether
        # the SQL executes against the real schema, so the tenant is given the
        # answered-question state first.
        cur.execute("insert into crm_sync_state (tenant_id, provider,"
                    " opportunities_synced_at) values"
                    " (zolts_internal.current_tenant(), 'test-crm', now())")
        for path in programs:
            program = dsl.load(path)
            try:
                audience.includes(cur, program.spec, program.key,
                                  "00000000-0000-0000-0000-000000000000")
            except audience.AudienceError as exc:
                broken.append(f"{path.name}: {exc}")

    assert not broken, (
        "a shipped program has an audience that cannot run:\n  " + "\n  ".join(broken))
