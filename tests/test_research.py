"""The research dossier: the last priced action nothing could execute.

`docs/12` prices it at 20 credits — the most expensive line in the list — and
`docs/08` gives the Researcher a role. The word existed in this runtime only as
a local variable inside the copywriter, holding evidence for one email and
discarded when the email was written.

Most of what is asserted here is about *not* spending. At twenty credits the
interesting properties are the ones that stop a call: no evidence, no budget,
no model, and a dossier that is still the answer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from runtime import research
from runtime.agents import researcher
from runtime.agents.client import Completion, ModelUnavailable
from runtime.agents.spend import Alternative, SpendVerdict
from runtime.repo import entities
from fastapi.testclient import TestClient

from runtime.api.app import create_app
from runtime.provision import issue_api_key
from tests.conftest import requires_db


@pytest.fixture
def client(db):
    return TestClient(create_app(db), raise_server_exceptions=False)


@pytest.fixture
def key(db, tenant):
    return issue_api_key(db, str(tenant["id"]), "test", []).token

# Inside the 180-day evidence window by construction: a fixed calendar date
# drifts out of it and the test then fails for the wrong reason.
NOW = datetime.now(timezone.utc) - timedelta(days=3)

BRIEF = ("Northwind is the account. Northwind has 201-500 employees. "
         "Dana Cruz is Head of RevOps at Northwind. "
         "This usually means the reporting layer is about to be rebuilt.")


@dataclass
class StubClient:
    """A model that returns what the test tells it to."""
    text: str = BRIEF
    model: str = "claude-opus-5"
    tokens: int = 3100
    refused: bool = False
    fails: bool = False
    calls: list = field(default_factory=list)

    @property
    def routed(self) -> bool:
        return False

    def count_tokens(self, *, system, messages, model=None):
        return self.tokens

    def complete(self, *, system, messages, model=None, max_tokens=2000, effort="medium"):
        if self.fails:
            raise ModelUnavailable("stub refuses to connect")
        self.calls.append(model or self.model)
        return Completion(text=self.text, model=model or self.model,
                          input_tokens=self.tokens, output_tokens=210,
                          stop_reason="refusal" if self.refused else "end_turn",
                          refused=self.refused)


@dataclass
class StubGuard:
    """A spend guard with a fixed answer.

    The real guard has its own suite; what is under test here is the ordering
    around it, and a test that needs `trazum-mcp` installed to assert that a
    refusal costs nothing is a test that silently stops running.
    """
    verdict: str = "yes"
    usd: float = 0.031
    alternative: str | None = None

    def check(self, **kwargs) -> SpendVerdict:
        alternatives = ([Alternative("cheaper-model", self.alternative, 0.02, True)]
                        if self.alternative else [])
        return SpendVerdict(self.verdict, self.usd, None, "stub", alternatives)


def _account(cur, tid: str, *, name="Northwind", band="201-500"):
    return entities.upsert_account(cur, tid, name=name,
                                   domain=f"{uuid.uuid4().hex[:8]}.com",
                                   country="ES", employee_band=band)


def _contact(cur, tid: str, account_id: str, *, name="Dana Cruz", title="Head of RevOps"):
    person = entities.upsert_person(cur, tid, email=f"{uuid.uuid4().hex[:8]}@example.com",
                                    full_name=name, country="ES")
    entities.link(cur, tid, str(person["id"]), str(account_id), buying_role="economic",
                  title=title)
    return person


def _signal(cur, tid: str, account_id: str, *, when=NOW, type="funding.round"):
    cur.execute(
        "insert into signal (tenant_id, entity_type, entity_id, type, strength,"
        " half_life_h, source, legal_basis, payload, observed_at, ingested_at)"
        " values (%s,'account',%s,%s,0.9,720,'test','legitimate_interest',"
        " '{\"stage\": \"series_a\"}', %s, %s) returning id",
        (tid, str(account_id), type, when, when))
    return cur.fetchone()["id"]


# -- the evidence is retrieved, and every item names its subject ----------

@requires_db
def test_every_piece_of_evidence_names_its_own_subject(db, tenant):
    """The defect that made the copywriter's first real messages unsupportable:
    a citation reading "raised 9,000,000" supports no sentence, because the
    verifier asks one source to cover a whole sentence and a sentence names who
    did the thing."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        account = _account(cur, tid)
        _contact(cur, tid, account["id"])
        _signal(cur, tid, account["id"])
        items, newest, seen = research.evidence_for(cur, dict(account))

    assert seen == 1 and newest == NOW
    assert all("Northwind" in item.text or "Dana" in item.text for item in items), \
        [i.text for i in items]


@requires_db
def test_what_has_already_been_tried_is_part_of_the_evidence(db, tenant, fake):
    """A brief that opens with an approach somebody used a fortnight ago is
    worse than no brief, and this is the only place the runtime can know it."""
    from tests.test_runtime_engine import (DISPATCH_SPEC, _account_with_contact,
                                           _ingest, _publish)
    from runtime.engine.worker import Worker
    from runtime.provision import store_connection
    from tests.conftest import SECRET

    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=DISPATCH_SPEC)
        account, _ = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=70)
    Worker(db, secret_key=SECRET, batch=10).tick([tid])

    with db.tenant_tx(tid) as cur:
        items, _, _ = research.evidence_for(cur, dict(account))
    assert any("has been sent" in item.text for item in items), [i.text for i in items]


# -- the four ways it declines to spend -----------------------------------

@requires_db
def test_an_account_nothing_is_known_about_is_refused_before_the_call(db, tenant):
    """Twenty credits for a confident description of a company nobody has data
    on is the exact purchase this layer exists to prevent."""
    client = StubClient()
    result = researcher.research(client=client, guard=StubGuard(), evidence=[],
                                 consumed_usd=1.0, limit_usd=40.0)
    assert result.state == "refused" and not client.calls
    assert "no evidence" in result.refused


@requires_db
def test_no_model_configured_writes_no_dossier_and_bills_nothing(db, tenant):
    """Fail closed. A deployment with no model must not store an empty document
    and charge for it."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        account = _account(cur, tid)
        built = research.build(cur, dict(tenant), dict(account), client=None, guard=None)
        cur.execute("select count(*) as n from dossier")
        stored = int(cur.fetchone()["n"])

    assert built.state == "refused" and built.credits == 0.0
    assert stored == 0, "nothing was written, so nothing is stored"


@requires_db
def test_a_tenant_at_their_ceiling_is_stopped_before_the_model_call(db, tenant):
    """Checked before, not after: at 20 credits a tenant who finds out
    afterwards has already been charged."""
    from runtime import metering

    tid = str(tenant["id"])
    client = StubClient()
    with db.tenant_tx(tid) as cur:
        account = _account(cur, tid)
        _contact(cur, tid, account["id"])
        cur.execute("select * from tenant where id = %s", (tid,))
        metering.meter(cur, dict(cur.fetchone()), kind="email.send", units=15_000)
        cur.execute("select * from tenant where id = %s", (tid,))
        built = research.build(cur, dict(cur.fetchone()), dict(account),
                               client=client, guard=StubGuard())

    assert built.state == "refused" and "budget" in built.reason
    assert not client.calls, "the model was asked despite there being no budget"


@requires_db
def test_the_spend_guard_refusing_costs_nothing(db, tenant):
    """A refused call produced no document, so there is nothing to charge for.
    Same rule as an enrichment miss, and for the same reason."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        account = _account(cur, tid)
        _contact(cur, tid, account["id"])
        built = research.build(cur, dict(tenant), dict(account),
                               client=StubClient(), guard=StubGuard(verdict="no"))
        cur.execute("select coalesce(sum(billed_credits),0) as c from cost_event"
                    " where kind = 'agent.dossier'")
        billed = float(cur.fetchone()["c"])

    assert built.state == "refused" and built.credits == 0.0
    assert billed == 0.0
    with db.tenant_tx(tid) as cur:
        # Stored, so the next caller learns why without paying to find out.
        assert research.latest(cur, str(account["id"]))["state"] == "refused"


# -- what it costs when it does run ---------------------------------------

@requires_db
def test_a_written_dossier_costs_twenty_credits(db, tenant):
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        account = _account(cur, tid)
        _contact(cur, tid, account["id"])
        built = research.build(cur, dict(tenant), dict(account),
                               client=StubClient(), guard=StubGuard())
        cur.execute("select * from cost_event where kind = 'agent.dossier'")
        event = dict(cur.fetchone())

    assert built.state in {"complete", "thin"}
    assert built.credits == 20.0
    assert float(event["billed_credits"]) == 20.0
    assert event["cost_micros"] > 0, "a model call is never accounted as free"


@requires_db
def test_a_claim_nothing_supports_is_struck_out_and_kept_as_a_finding(db, tenant):
    """A message that loses a sentence is a weaker message; a dossier that
    loses one is a document with a hole, and the hole is the finding."""
    tid = str(tenant["id"])
    invented = BRIEF + " Northwind raised 40 million dollars in February."
    with db.tenant_tx(tid) as cur:
        account = _account(cur, tid)
        _contact(cur, tid, account["id"])
        research.build(cur, dict(tenant), dict(account),
                       client=StubClient(text=invented), guard=StubGuard())
        stored = research.latest(cur, str(account["id"]))

    assert "40 million" not in stored["body"]
    assert any("40 million" in claim for claim in stored["dropped"]), stored["dropped"]
    assert stored["state"] == "thin"


# -- a current dossier is served, not rewritten ---------------------------

@requires_db
def test_a_current_dossier_is_served_rather_than_bought_again(db, tenant):
    """Charging per request would make the console's own account page the most
    expensive screen in the product."""
    tid = str(tenant["id"])
    client = StubClient()
    with db.tenant_tx(tid) as cur:
        account = _account(cur, tid)
        _contact(cur, tid, account["id"])
        first = research.build(cur, dict(tenant), dict(account),
                               client=client, guard=StubGuard())
        second = research.build(cur, dict(tenant), dict(account),
                                client=client, guard=StubGuard())
        cur.execute("select count(*) as n from cost_event where kind = 'agent.dossier'")
        events = int(cur.fetchone()["n"])

    assert first.credits == 20.0 and second.credits == 0.0
    assert second.reused and second.state == first.state
    assert len(client.calls) == 1 and events == 1


@requires_db
def test_a_signal_arriving_afterwards_makes_the_dossier_stale(db, tenant):
    """Freshness is a comparison, not a clock. A dossier written a month ago on
    an account that has done nothing since is still the answer."""
    tid = str(tenant["id"])
    client = StubClient()
    with db.tenant_tx(tid) as cur:
        account = _account(cur, tid)
        _contact(cur, tid, account["id"])
        _signal(cur, tid, account["id"], when=NOW - timedelta(days=30))
        research.build(cur, dict(tenant), dict(account), client=client, guard=StubGuard())

        # Something happened. The stored answer no longer saw everything.
        _signal(cur, tid, account["id"], when=datetime.now(timezone.utc),
                type="hiring.role_opened")
        again = research.build(cur, dict(tenant), dict(account),
                               client=client, guard=StubGuard())

    assert not again.reused and again.credits == 20.0
    assert len(client.calls) == 2


@requires_db
def test_force_rewrites_a_current_dossier_and_pays_again(db, tenant):
    """An operator who does not believe one must be able to say so, and it is
    off by default because the reason a dossier is reused is that nothing has
    changed."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        account = _account(cur, tid)
        _contact(cur, tid, account["id"])
        research.build(cur, dict(tenant), dict(account),
                       client=StubClient(), guard=StubGuard())
        forced = research.build(cur, dict(tenant), dict(account),
                                client=StubClient(), guard=StubGuard(), force=True)

    assert not forced.reused and forced.credits == 20.0


# -- what the operator sees -----------------------------------------------

@requires_db
def test_coverage_reports_staleness_beside_it(db, tenant):
    """Coverage alone lies: a dossier on every account, all written before this
    quarter's news, is full coverage and no knowledge."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        fresh = _account(cur, tid, name="Fresh Co")
        stale = _account(cur, tid, name="Stale Co")
        for account in (fresh, stale):
            _contact(cur, tid, account["id"])
            research.build(cur, dict(tenant), dict(account),
                           client=StubClient(), guard=StubGuard())
        _signal(cur, tid, stale["id"], when=datetime.now(timezone.utc))
        report = research.coverage(cur)

    assert report["accounts"] == 2 and report["withDossier"] == 2
    assert report["stale"] == 1


@requires_db
def test_the_prospects_view_says_which_accounts_have_one(db, tenant):
    """An operator picking accounts to research must see which already have a
    dossier, or the screen buys the same twenty-credit document twice."""
    from runtime.api import console

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        done = _account(cur, tid, name="Researched Co")
        _account(cur, tid, name="Untouched Co")
        _contact(cur, tid, done["id"])
        research.build(cur, dict(tenant), dict(done),
                       client=StubClient(), guard=StubGuard())
        view = console.prospects_view(cur)

    states = {a["name"]: a["dossier"] for a in view["accounts"]}
    assert states["Researched Co"] in {"complete", "thin"}
    assert states["Untouched Co"] is None
    assert view["research"]["withDossier"] == 1


@requires_db
def test_research_over_http_needs_a_write_scope(db, client, tenant):
    """It is the most expensive action in the price list."""
    read_only = issue_api_key(db, str(tenant["id"]), "read", ["read"]).token
    response = client.post("/v1/research", json={"account_ids": [str(uuid.uuid4())]},
                           headers={"authorization": f"Bearer {read_only}"})
    assert response.status_code == 403


@requires_db
def test_a_dossier_is_readable_with_what_was_struck_out_of_it(db, client, key, tenant):
    """The dropped claims are returned rather than hidden: what the model wanted
    to say and could not support is what tells an operator how much to believe."""
    tid = str(tenant["id"])
    invented = BRIEF + " Northwind raised 40 million dollars in February."
    with db.tenant_tx(tid) as cur:
        account = _account(cur, tid)
        _contact(cur, tid, account["id"])
        research.build(cur, dict(tenant), dict(account),
                       client=StubClient(text=invented), guard=StubGuard())

    response = client.get(f"/v1/research/{account['id']}",
                          headers={"authorization": f"Bearer {key}"})
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "thin" and body["current"] is True
    assert any("40 million" in claim for claim in body["dropped"])


@requires_db
def test_reading_a_dossier_that_was_never_written_is_a_404(db, client, key):
    response = client.get(f"/v1/research/{uuid.uuid4()}",
                          headers={"authorization": f"Bearer {key}"})
    assert response.status_code == 404


@requires_db
def test_two_dossiers_written_in_one_transaction_are_still_ordered(db, tenant):
    """`now()` is the transaction timestamp, so a rewrite in the same
    transaction as the original shares its `created_at` exactly.

    Ordering on it would return whichever row the planner reached first — an
    undecidable answer from a table whose entire purpose is "the current one
    for this account". The sequence decides instead.
    """
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        account = _account(cur, tid)
        _contact(cur, tid, account["id"])
        research.build(cur, dict(tenant), dict(account),
                       client=StubClient(text=BRIEF), guard=StubGuard())
        second = BRIEF + " Northwind is based in ES."
        research.build(cur, dict(tenant), dict(account),
                       client=StubClient(text=second), guard=StubGuard(), force=True)

        cur.execute("select count(distinct created_at) as n from dossier")
        assert int(cur.fetchone()["n"]) == 1, "the premise of this test no longer holds"
        assert "based in ES" in research.latest(cur, str(account["id"]))["body"]
