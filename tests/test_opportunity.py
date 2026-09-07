"""The open-deal exclusion, from the CRM to the audience that uses it.

Program 01 carried this comment for as long as the program existed:

    The exclusion that is missing, deliberately and visibly: accounts with an
    open opportunity. There is no `opportunity` table and no CRM sync that
    would fill one, so the clause is absent rather than written as something
    that always passes.

This is that clause, and the machinery that makes writing it honest. The test
that matters most is not the one that proves an open deal excludes an account
— it is `test_an_audience_that_needs_deals_refuses_when_nothing_has_delivered`,
because an empty `opportunity` table and a tenant with no open deals produce
exactly the same SQL result, and only one of them means the account is safe to
contact.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from runtime.connectors import sync
from runtime.connectors.crm import (Capabilities, Consent, CrmAccount, CrmContact,
                                    CrmOpportunity, DealStatus)
from runtime.engine import audience, enroll
from tests.conftest import requires_db
from zolts.catalog import load_catalog

NOW = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


class _CrmWithDeals:
    """Two accounts; one of them is in a live deal."""

    capabilities = Capabilities(provider="dealcrm", reads_opt_out=True,
                                reads_opportunities=True)

    def __init__(self, deals: list[CrmOpportunity] | None = None) -> None:
        self._deals = deals if deals is not None else [
            CrmOpportunity(external_id="d-1", account_external_id="a-2",
                           name="Fabrikam renewal", stage="Negotiation",
                           status=DealStatus.OPEN),
            CrmOpportunity(external_id="d-2", account_external_id="a-1",
                           name="Contoso pilot", stage="Closed Won",
                           status=DealStatus.WON)]

    def accounts(self, credential: str):
        yield CrmAccount(external_id="a-1", name="Contoso Data", domain="contoso.test",
                         country="ES", employee_band="51-200", industry="software")
        yield CrmAccount(external_id="a-2", name="Fabrikam Logistics",
                         domain="fabrikam.test", country="ES",
                         employee_band="51-200", industry="software")

    def contacts(self, credential: str):
        yield CrmContact(external_id="c-1", email="rio@contoso.test", country="ES",
                         account_external_id="a-1", consent=Consent.ALLOWED)

    def opportunities(self, credential: str):
        yield from self._deals


class _CrmWithoutDeals:
    """A CRM that cannot answer the question. Not an error — a gap."""

    capabilities = Capabilities(provider="dealblindcrm", reads_opt_out=True,
                                reads_opportunities=False)

    def accounts(self, credential: str):
        yield CrmAccount(external_id="a-1", name="Contoso Data", domain="contoso.test",
                         country="ES", employee_band="51-200", industry="software")

    def contacts(self, credential: str):
        yield CrmContact(external_id="c-1", email="rio@contoso.test", country="ES",
                         account_external_id="a-1", consent=Consent.ALLOWED)

    def opportunities(self, credential: str):
        return iter(())


def _flagship() -> dict:
    """The shipped program, because the clause under test is written in it."""
    for program in load_catalog().programs:
        if program.key == "series-a-hiring-surge":
            return program.spec
    raise AssertionError("the flagship program is gone")


def _account(cur, name: str) -> str:
    cur.execute("select id from account where name = %s", (name,))
    return str(cur.fetchone()["id"])


# -- the clause exists ----------------------------------------------------

def test_the_shipped_program_still_excludes_open_deals():
    """The regression guard for the clause itself. Deleting it is a one-line
    edit to a YAML file that no other test would notice."""
    sql = _flagship()["audience"]["sql"]
    assert audience.relies_on_deals(sql), (
        "the flagship program no longer excludes accounts with an open deal")
    assert "status = 'open'" in sql, (
        "the exclusion must filter on open deals; excluding every deal ever "
        "closed would remove every account the company has ever sold to")


# -- the sync -------------------------------------------------------------

@requires_db
def test_deals_are_synced_and_linked_to_their_account(db, tenant):
    report = sync.pull(db, str(tenant["id"]), "credential", source=_CrmWithDeals())
    assert report.opportunities == 2 and report.open_deals == 1
    with db.tenant_tx(tenant["id"]) as cur:
        cur.execute("select o.status, a.name from opportunity o"
                    " join account a on a.id = o.account_id order by a.name")
        assert [(r["status"], r["name"]) for r in cur.fetchall()] == [
            ("won", "Contoso Data"), ("open", "Fabrikam Logistics")]


@requires_db
def test_a_re_sync_moves_a_deal_rather_than_writing_a_second(db, tenant):
    """A deal that closes between two syncs must stop excluding its account.
    An insert-only sync would leave the open row beside the won one and the
    account would never be contactable again."""
    tid = str(tenant["id"])
    sync.pull(db, tid, "credential", source=_CrmWithDeals())
    closed = [CrmOpportunity(external_id="d-1", account_external_id="a-2",
                             name="Fabrikam renewal", stage="Closed Won",
                             status=DealStatus.WON)]
    sync.pull(db, tid, "credential", source=_CrmWithDeals(deals=closed))
    with db.tenant_tx(tid) as cur:
        cur.execute("select count(*) as n from opportunity where crm_id = 'd-1'")
        assert cur.fetchone()["n"] == 1
        cur.execute("select count(*) as n from opportunity"
                    " where account_id = %s and status = 'open'",
                    (_account(cur, "Fabrikam Logistics"),))
        assert cur.fetchone()["n"] == 0


@requires_db
def test_a_deal_whose_account_is_unknown_is_kept_and_counted(db, tenant):
    """Dropping it would quietly reopen the hole: the account may arrive on
    the next sync, and a deal this runtime threw away never comes back."""
    orphan = [CrmOpportunity(external_id="d-9", account_external_id="a-404",
                             status=DealStatus.OPEN)]
    report = sync.pull(db, str(tenant["id"]), "credential",
                       source=_CrmWithDeals(deals=orphan))
    assert report.orphan_deals == 1 and report.opportunities == 1
    with db.tenant_tx(tenant["id"]) as cur:
        cur.execute("select account_id from opportunity where crm_id = 'd-9'")
        assert cur.fetchone()["account_id"] is None


@requires_db
def test_a_crm_that_cannot_read_deals_says_so_in_the_report(db, tenant):
    """Said once, loudly, in the report an operator reads — the same rule the
    connector layer already applies to opt-out state."""
    report = sync.pull(db, str(tenant["id"]), "credential", source=_CrmWithoutDeals())
    assert any("already in a deal" in caveat for caveat in report.caveats)
    with db.tenant_tx(tenant["id"]) as cur:
        cur.execute("select count(*) as n from crm_sync_state"
                    " where opportunities_synced_at is not null")
        assert cur.fetchone()["n"] == 0, (
            "a source that cannot read deals must not be recorded as having "
            "answered the question")


@requires_db
def test_a_deal_with_no_close_date_is_stored(db, tenant):
    """Every CRM formats dates differently and Postgres parses all of them.
    What it will not parse is the empty string a CRM sends for a deal that has
    not closed, which is most of them."""
    open_deal = [CrmOpportunity(external_id="d-3", account_external_id="a-1",
                                status=DealStatus.OPEN, opened_at="2026-01-15",
                                closed_at="")]
    sync.pull(db, str(tenant["id"]), "credential",
              source=_CrmWithDeals(deals=open_deal))
    with db.tenant_tx(tenant["id"]) as cur:
        cur.execute("select opened_at, closed_at from opportunity where crm_id = 'd-3'")
        row = cur.fetchone()
        assert row["opened_at"] is not None and row["closed_at"] is None


# -- the audience ---------------------------------------------------------

@requires_db
def test_an_account_in_an_open_deal_is_not_in_the_audience(db, tenant):
    tid = str(tenant["id"])
    sync.pull(db, tid, "credential", source=_CrmWithDeals())
    spec = _flagship()
    with db.tenant_tx(tid) as cur:
        assert audience.includes(cur, spec, "flagship", _account(cur, "Contoso Data")), (
            "the account whose only deal is won belongs in the audience")
        assert not audience.includes(cur, spec, "flagship",
                                     _account(cur, "Fabrikam Logistics"))


@requires_db
def test_an_audience_that_needs_deals_refuses_when_nothing_has_delivered(db, tenant):
    """The test this whole file exists for.

    An empty `opportunity` table and a tenant with no open deals are the same
    query result. Reading them the same way turns the exclusion into a clause
    that always passes, which is protection that is not there.
    """
    tid = str(tenant["id"])
    sync.pull(db, tid, "credential", source=_CrmWithoutDeals())
    with db.tenant_tx(tid) as cur:
        with pytest.raises(audience.AudienceError, match="no connected CRM"):
            audience.includes(cur, _flagship(), "flagship", _account(cur, "Contoso Data"))


@requires_db
def test_once_a_crm_delivers_deals_the_audience_runs(db, tenant):
    """The other half: the refusal has to lift, or connecting a CRM would
    never make a program work again."""
    tid = str(tenant["id"])
    sync.pull(db, tid, "credential", source=_CrmWithoutDeals())
    sync.pull(db, tid, "credential", source=_CrmWithDeals())
    with db.tenant_tx(tid) as cur:
        assert audience.includes(cur, _flagship(), "flagship", _account(cur, "Contoso Data"))


# -- enrolment ------------------------------------------------------------

@requires_db
def test_enrolment_refuses_and_records_why_when_deals_are_unanswerable(db, tenant, fake):
    """A program that has stopped enrolling because nothing can answer its
    audience looks exactly like a program with no matching signals. The
    difference is worth an audit row."""
    from runtime.repo import programs

    tid = str(tenant["id"])
    sync.pull(db, tid, "credential", source=_CrmWithoutDeals())
    spec = _flagship()
    with db.tenant_tx(tid) as cur:
        row = programs.publish(cur, tid, key="flagship", version="1.0.0", spec=spec,
                               spec_hash=uuid.uuid4().hex, status="draft")
        programs.activate(cur, str(row["id"]))
        account_id = _account(cur, "Contoso Data")
        result = enroll.ingest(
            cur, tid, entity_type="account", entity_id=account_id,
            type="funding.round", strength=0.9, half_life_h=720, source="test",
            legal_basis="legitimate_interest",
            payload={"stage": "series_a", "amount_usd": 9_000_000},
            observed_at=NOW, now=NOW)
        assert result.enrollments == []
        cur.execute("select detail from audit_log where action = 'enrollment.refused'")
        refusals = [dict(r) for r in cur.fetchall()]
    assert refusals, "an audience that cannot be answered must say so"
    assert "no connected CRM" in refusals[0]["detail"]["reason"]


@requires_db
def test_an_account_in_an_open_deal_is_never_enrolled(db, tenant, fake):
    """End to end, on the shipped program: the signal matches, the score
    matches, the country and size and industry match, and the account is left
    alone because the sales team is already in it."""
    from runtime.repo import programs

    tid = str(tenant["id"])
    sync.pull(db, tid, "credential", source=_CrmWithDeals())
    with db.tenant_tx(tid) as cur:
        row = programs.publish(cur, tid, key="flagship", version="1.0.0",
                               spec=_flagship(), spec_hash=uuid.uuid4().hex,
                               status="draft")
        programs.activate(cur, str(row["id"]))
        signal = dict(entity_type="account", type="funding.round", strength=0.9,
                      half_life_h=720, source="test",
                      legal_basis="legitimate_interest",
                      payload={"stage": "series_a", "amount_usd": 9_000_000},
                      observed_at=NOW, now=NOW)
        clean = enroll.ingest(cur, tid, entity_id=_account(cur, "Contoso Data"),
                              dedupe_key=f"a-{uuid.uuid4().hex}", **signal)
        in_deal = enroll.ingest(cur, tid, entity_id=_account(cur, "Fabrikam Logistics"),
                                dedupe_key=f"b-{uuid.uuid4().hex}", **signal)
    assert clean.enrollments, "the audience matched nobody; the test proves nothing"
    assert in_deal.enrollments == []
