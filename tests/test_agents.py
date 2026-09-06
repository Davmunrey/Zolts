"""The agent layer.

The spend guard here is the real Trazum MCP server — it never makes a provider
call to answer, so exercising it costs nothing and testing against a stub would
test the stub. The model is faked: a test suite that generates text bills the
person running it, and what is under test is the ordering around the call, not
the model.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

import pytest

from runtime.agents import copywriter
from runtime.agents.client import Completion, ModelUnavailable
from runtime.agents.spend import SpendGuard, SpendVerdict
from zolts import evals
from zolts.provenance import Evidence, Source

EVIDENCE = [
    Evidence(Source.RETRIEVED, "Northwind opened four RevOps roles last quarter.", "li#1"),
    Evidence(Source.PROOF, "Kestrel cut onboarding time by 40 percent.", "proof.kestrel"),
]
OPT_OUT = "Reply unsubscribe and I will stop."

GOOD_DRAFT = ("Hi Dana. Northwind opened four RevOps roles last quarter, which usually "
              "means the reporting layer is about to be rebuilt. Kestrel cut onboarding "
              "time by 40 percent through the same transition. Worth twenty minutes next "
              "week? Reply unsubscribe and I will stop.")

has_guard = pytest.mark.skipif(shutil.which("trazum-mcp") is None,
                               reason="trazum-mcp is not installed")


@dataclass
class StubClient:
    """A model that returns what the test tells it to."""
    text: str = GOOD_DRAFT
    model: str = "claude-opus-5"
    tokens: int = 4200
    refused: bool = False
    fails: bool = False
    calls: list[str] = None

    def __post_init__(self):
        self.calls = []

    @property
    def routed(self) -> bool:
        return False

    def count_tokens(self, *, system, messages, model=None):
        return self.tokens

    def complete(self, *, system, messages, model=None, max_tokens=2000, effort="medium"):
        if self.fails:
            raise ModelUnavailable("stub refuses to connect")
        self.calls.append(model or self.model)
        return Completion(text=self.text, model=model or self.model, input_tokens=self.tokens,
                          output_tokens=180,
                          stop_reason="refusal" if self.refused else "end_turn",
                          refused=self.refused)


@pytest.fixture(scope="module")
def guard():
    g = SpendGuard()
    yield g
    g.close()


def _draft(client, guard, **kwargs):
    kwargs.setdefault("tier", "t2")
    kwargs.setdefault("policy_allows", True)
    kwargs.setdefault("tenant_enabled", True)
    kwargs.setdefault("consumed_usd", 1.0)
    kwargs.setdefault("limit_usd", 40.0)
    return copywriter.draft(client=client, guard=guard, evidence=EVIDENCE,
                            opt_out=OPT_OUT, **kwargs)


# -- the spend guard is real ---------------------------------------------

@has_guard
def test_a_priced_call_inside_budget_proceeds(guard):
    result = _draft(StubClient(), guard)
    assert result.spend.allowed and result.spend.estimated_usd > 0
    assert result.cost_micros > 0, "a model call is never accounted as free"


@has_guard
def test_no_budget_means_no_call(guard):
    """`cannot-tell` is not a yes. It is nobody having set a ceiling."""
    client = StubClient()
    result = _draft(client, guard, consumed_usd=None, limit_usd=None,
                    allow_downgrade=False)
    assert result.spend.verdict == "cannot-tell"
    assert result.blocked and client.calls == [], "the model must not have been called"


@has_guard
def test_a_refusal_carries_a_lever_and_the_agent_takes_it(guard):
    """The guard answers a no with the cheaper model that still fits.

    Ignoring it would make the alternatives decorative and the sequence would
    simply stop.
    """
    # A window where the frontier model no longer fits and the cheap one still
    # does. Measured against the real guard rather than assumed: outside it the
    # downgrade is not the behaviour under test.
    client = StubClient()
    result = _draft(client, guard, consumed_usd=39.98, limit_usd=40.0)
    assert result.model == "claude-haiku-4-5", "it should have taken the lever"
    assert client.calls == [result.model], "and called the cheaper model, not the first one"
    assert result.meta["downgraded"] is True
    assert result.spend.allowed


@has_guard
def test_a_budget_too_small_for_any_model_makes_no_call(guard):
    """Taking the lever is not the same as ignoring the answer."""
    client = StubClient()
    result = _draft(client, guard, consumed_usd=39.9999, limit_usd=40.0)
    assert result.blocked and client.calls == []


@has_guard
def test_an_exhausted_budget_with_downgrade_off_makes_no_call(guard):
    client = StubClient()
    result = _draft(client, guard, consumed_usd=1_000_000.0, limit_usd=40.0,
                    allow_downgrade=False)
    assert result.blocked and client.calls == []


# -- what happens to the generated text ----------------------------------

@has_guard
def test_an_unsupported_claim_is_stripped_before_the_message_is_scored(guard):
    invented = GOOD_DRAFT.replace("Worth twenty minutes",
                                  "We are the leading platform in Europe. Worth twenty minutes")
    result = _draft(StubClient(text=invented), guard)
    assert "leading platform" not in result.text
    assert "leading platform" in result.raw_text, "the raw draft is kept for the audit"


@has_guard
def test_an_invented_figure_never_auto_sends(guard):
    invented = GOOD_DRAFT.replace("Worth twenty minutes",
                                  "Teams like yours see a 312 percent return. Worth twenty minutes")
    result = _draft(StubClient(text=invented), guard)
    assert not result.gate.auto_send and result.state == "needs_human"
    assert "312 percent" not in result.text


@has_guard
def test_provenance_overrides_a_score_that_would_have_passed(guard):
    """A message can read perfectly, score above the threshold, and still have
    had a number removed from it. The score is an average; a fabricated figure
    is not the kind of thing an average should be allowed to outvote."""
    padded = (GOOD_DRAFT.replace("Worth twenty minutes",
                                 "Teams like yours see a 312 percent return. Worth twenty minutes")
              + " Northwind opened four RevOps roles last quarter."
              + " Kestrel cut onboarding time by 40 percent.")
    result = _draft(StubClient(text=padded), guard)
    assert result.gate.score is not None and result.gate.score >= 0.85, (
        "this test is only meaningful while the score alone would have passed")
    assert not result.gate.auto_send and "provenance" in result.gate.reason


@has_guard
def test_a_clean_draft_clears_tier_two(guard):
    result = _draft(StubClient(), guard)
    assert result.gate.auto_send and result.state == "approved"


@has_guard
def test_tier_one_is_always_a_human(guard):
    result = _draft(StubClient(), guard, tier="t1")
    assert not result.gate.auto_send and result.state == "needs_human"


@has_guard
def test_a_missing_opt_out_blocks_on_compliance(guard):
    result = _draft(StubClient(text=GOOD_DRAFT.replace(OPT_OUT, "")), guard)
    assert not result.gate.auto_send and "compliance" in result.gate.reason


@has_guard
def test_a_model_refusal_is_recorded_not_swallowed(guard):
    result = _draft(StubClient(refused=True), guard)
    assert result.blocked and "declined" in result.blocked
    assert result.cost_micros > 0, "a refused generation still cost tokens"


@has_guard
def test_an_unreachable_model_never_produces_an_approved_draft(guard):
    result = _draft(StubClient(fails=True), guard)
    assert result.state == "rejected" and not result.gate.auto_send


# -- fail-closed ---------------------------------------------------------

def test_an_unreachable_guard_refuses_rather_than_permitting():
    """A cost control that opens when it breaks is not a cost control."""
    broken = SpendGuard(command="definitely-not-a-real-binary")
    assert not broken.available
    verdict = broken.check(model="claude-opus-5", input_tokens=100, consumed_usd=0,
                           limit_usd=10)
    assert verdict.verdict == "cannot-tell" and not verdict.allowed


def test_a_prose_answer_is_not_read_as_permission():
    """The server answers a malformed request in prose. That is a caller bug,
    and it must not read as a yes."""
    from runtime.agents.spend import _parse

    verdict = _parse({"content": [{"type": "text", "text": 'spend_guard: unknown key "x"'}]})
    assert verdict.verdict == "cannot-tell" and not verdict.allowed


# -- wired into the runtime ----------------------------------------------

AGENT_SPEC = None  # built lazily; the engine tests own the base spec


def _agent_spec():
    from tests.test_runtime_engine import DISPATCH_SPEC

    spec = {k: v for k, v in DISPATCH_SPEC.items()}
    # A ceiling somebody set. Without one the spend guard answers cannot-tell
    # and no generation happens at all — see the test below, which locks that.
    spec["budget"] = {"monthly_credits": 4000, "on_exceed": "pause_and_alert"}
    spec["plays"] = {
        "t2": {"auto_send": True, "auto_send_requires": {"eval_score": 0.85},
               "steps": [{"step": "email_1", "channel": "email", "wait": "0d",
                          "agent": "copywriter"}]},
        "t1": {"auto_send": False, "steps": [{"step": "task_ae", "channel": "task"}]},
    }
    return spec


# What the seeded dossier actually contains: the account, the contact, and a
# recent funding signal. A draft is only supportable against the evidence that
# retrieval produced, so the fixture states both halves rather than hoping.
WIRED_DRAFT = (
    "Hi Dana. Dana Cruz works at Acme, and that is usually where the reporting layer "
    "breaks first. Acme just closed a funding round of 9000000. Worth twenty minutes "
    "next week? Reply unsubscribe and I will stop.")


def _seed(db, tenant, fake, guard, client):
    """A tenant with one enrolled account whose next step names an agent."""
    from runtime.engine.worker import Worker
    from runtime.provision import store_connection
    from tests.conftest import SECRET
    from tests.test_runtime_engine import _account_with_contact, _ingest, _publish

    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=_agent_spec())
        account, person = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=70)
        # The engine fixture pins its signal to a fixed date months in the
        # past, which is correct there and outside the agent's retrieval
        # window here. A generation needs a signal it can actually cite.
        _recent_signal(cur, tid, account["id"])
    worker = Worker(db, secret_key=SECRET, model_client=client, spend_guard=guard)
    return tid, worker, person


def _recent_signal(cur, tenant_id, account_id):
    from datetime import datetime, timezone
    import uuid as _uuid

    from runtime.repo import signals

    signals.record(cur, tenant_id, entity_type="account", entity_id=str(account_id),
                   type="funding.round", strength=0.9, half_life_h=720, source="test",
                   legal_basis="legitimate_interest",
                   payload={"stage": "series_a", "amount_usd": 9000000},
                   observed_at=datetime.now(timezone.utc),
                   dedupe_key=f"recent-{_uuid.uuid4().hex}")


@has_guard
def test_an_agent_step_produces_a_proposal_then_a_send(db, tenant, fake, guard):
    from runtime.repo import proposals

    tid, worker, _ = _seed(db, tenant, fake, guard, StubClient(text=WIRED_DRAFT))
    tick = worker.tick([tid])
    assert tick.succeeded >= 1, tick.errors

    with db.tenant_tx(tid) as cur:
        queued = proposals.counts(cur)
        cur.execute("select kind, state from action order by created_at")
        kinds = [(r["kind"], r["state"]) for r in cur.fetchall()]
    assert queued.get("dispatched") == 1, f"expected one promoted proposal, got {queued}"
    assert ("generate", "succeeded") in kinds
    assert any(k == "dispatch" for k, _ in kinds), "an approved proposal must queue a send"


@has_guard
def test_a_draft_that_fails_the_gate_never_queues_a_send(db, tenant, fake, guard):
    """The proposal is the record; the send is a separate, gated act."""
    from runtime.repo import proposals

    invented = WIRED_DRAFT.replace("Worth twenty minutes",
                                   "Teams like yours see a 312 percent return. Worth twenty minutes")
    tid, worker, _ = _seed(db, tenant, fake, guard, StubClient(text=invented))
    worker.tick([tid])
    with db.tenant_tx(tid) as cur:
        counts = proposals.counts(cur)
        cur.execute("select count(*) as n from action where kind = 'dispatch'")
        sends = cur.fetchone()["n"]
        assert proposals.queue(cur), "it must be waiting for a person"
    assert counts.get("needs_human") == 1 and sends == 0


@has_guard
def test_an_agent_step_without_a_model_fails_loudly(db, tenant, fake, guard):
    """Skipping the generation and sending an empty message is the one
    outcome worse than stopping."""
    from runtime.engine.worker import Worker
    from runtime.provision import store_connection
    from tests.conftest import SECRET
    from tests.test_runtime_engine import _account_with_contact, _ingest, _publish

    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=_agent_spec())
        account, _ = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=70)
    tick = Worker(db, secret_key=SECRET).tick([tid])   # no model client
    assert tick.cancelled == 1 and tick.succeeded == 0
    with db.tenant_tx(tid) as cur:
        cur.execute("select last_error from action where state = 'cancelled'")
        assert "no model client is configured" in cur.fetchone()["last_error"]


@has_guard
def test_a_generation_records_its_cost_against_the_program(db, tenant, fake, guard):
    tid, worker, _ = _seed(db, tenant, fake, guard, StubClient(text=WIRED_DRAFT))
    worker.tick([tid])
    with db.tenant_tx(tid) as cur:
        cur.execute("select kind, cost_micros from cost_event where kind = 'llm'")
        row = cur.fetchone()
    assert row and row["cost_micros"] > 0, "an LLM call is never accounted as free"


@has_guard
def test_a_program_with_no_declared_budget_cannot_run_an_agent(db, tenant, fake, guard):
    """`cannot-tell` is not a yes.

    A program that never declared a ceiling has no budget for a model call to
    fit inside, so the call is refused rather than made against a limit nobody
    set. The DSL schema requires a budget block; this is what happens when one
    is missing anyway.
    """
    from runtime.repo import proposals

    spec = _agent_spec()
    spec.pop("budget")
    from runtime.engine.worker import Worker
    from runtime.provision import store_connection
    from tests.conftest import SECRET
    from tests.test_runtime_engine import _account_with_contact, _ingest, _publish

    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        _publish(cur, tid, spec=spec)
        account, _ = _account_with_contact(cur, tid)
        _ingest(cur, tid, account["id"], score=70)
    client = StubClient()
    Worker(db, secret_key=SECRET, model_client=client, spend_guard=guard).tick([tid])

    with db.tenant_tx(tid) as cur:
        cur.execute("select state, gate_reason from proposal")
        row = cur.fetchone()
        cur.execute("select count(*) as n from action where kind = 'dispatch'")
        assert cur.fetchone()["n"] == 0
    assert row["state"] == "rejected" and "no-budget-configured" in row["gate_reason"]
    assert client.calls == [], "the model must not have been called"


@has_guard
def test_a_suppressed_contact_is_not_drafted_for(db, tenant, fake, guard):
    """The policy gate runs before a word is generated: drafting for a contact
    the runtime may not write to spends money on something unusable."""
    from runtime.repo import entities, proposals

    tid, worker, person = _seed(db, tenant, fake, guard, StubClient(text=WIRED_DRAFT))
    with db.tenant_tx(tid) as cur:
        entities.suppress(cur, tid, "email", str(person["email"]), "unsubscribed", "test")
    worker.tick([tid])
    with db.tenant_tx(tid) as cur:
        counts = proposals.counts(cur)
        cur.execute("select count(*) as n from action where kind = 'dispatch'")
        assert cur.fetchone()["n"] == 0
    assert "dispatched" not in counts


@has_guard
def test_a_dispatched_proposal_cannot_be_re_decided(db, tenant, fake, guard):
    """Two reviewers opening the same item is ordinary. The second must not be
    able to approve something already sent."""
    from runtime.repo import proposals

    tid, worker, _ = _seed(db, tenant, fake, guard, StubClient(text=WIRED_DRAFT))
    worker.tick([tid])
    with db.tenant_tx(tid) as cur:
        cur.execute("select id, state from proposal")
        row = cur.fetchone()
        assert row["state"] == "dispatched"
        again = proposals.decide(cur, str(row["id"]), state="approved",
                                 approved_by="a-second-reviewer")
    assert again is None, "a dispatched proposal is terminal"


@has_guard
def test_the_promoted_send_carries_the_verified_body_not_the_raw_draft(db, tenant, fake, guard):
    """What reaches the provider is what survived provenance, not what the
    model wrote."""
    invented = WIRED_DRAFT.replace("Worth twenty minutes",
                                   "Acme is the leading logistics platform. Worth twenty minutes")
    tid, worker, _ = _seed(db, tenant, fake, guard, StubClient(text=invented))
    worker.tick([tid])
    with db.tenant_tx(tid) as cur:
        cur.execute("select payload from action where kind = 'dispatch'")
        row = cur.fetchone()
        cur.execute("select content from proposal")
        content = cur.fetchone()["content"]
    assert "leading logistics platform" in content["raw"], "the raw draft is kept"
    if row:  # promoted only if the gate still passed
        assert "leading logistics platform" not in row["payload"]["step"]["body"]
