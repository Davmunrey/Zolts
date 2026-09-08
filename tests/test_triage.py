"""Reading what a reply actually says.

Every reply used to be recorded as `reply_positive`. Somebody writing "take me
off your list" was counted as a conversion, left contactable, and folded into
the lift the product reports — wrong in the one direction a measurement product
cannot afford, and invisible to the compliance layer.
"""

from __future__ import annotations

import pytest

from runtime.agents.spend import SpendVerdict
from runtime.agents.triage import Triage, classify, parse
from tests.conftest import requires_db


class FakeCompletion:
    def __init__(self, text: str) -> None:
        self.text = text
        self.cost_micros = 1200


class FakeClient:
    model = "claude-haiku-4-5-20251001"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def count_tokens(self, **_kwargs) -> int:
        return 300

    def complete(self, **_kwargs) -> FakeCompletion:
        self.calls += 1
        return FakeCompletion(self.reply)


class AllowingGuard:
    def check(self, **_kwargs) -> SpendVerdict:
        return SpendVerdict("yes", 0.0004, None, "pricing")


class RefusingGuard:
    def check(self, **_kwargs) -> SpendVerdict:
        return SpendVerdict("no", 4.0, "over the ceiling", "pricing")


REPLY = "Thanks but please take me off your list. Not interested."


# -- parsing -------------------------------------------------------------

def test_a_verdict_is_read_out_of_json():
    verdict, quote, error = parse('{"verdict": "unsubscribe", "quote": "take me off"}')
    assert (verdict, quote, error) == ("unsubscribe", "take me off", None)


def test_a_fenced_block_is_read():
    verdict, _, error = parse('```json\n{"verdict": "positive", "quote": "call me"}\n```')
    assert verdict == "positive" and error is None


def test_an_unknown_verdict_is_refused():
    verdict, _, error = parse('{"verdict": "maybe", "quote": "x"}')
    assert verdict is None
    assert "unknown verdict" in error


def test_prose_is_refused():
    verdict, _, error = parse("I think this person wants to unsubscribe.")
    assert verdict is None and "not JSON" in error


# -- classifying ---------------------------------------------------------

def test_a_quoted_verdict_is_usable():
    result = classify(client=FakeClient(
        '{"verdict": "unsubscribe", "quote": "please take me off your list"}'),
        guard=AllowingGuard(), text=REPLY, consumed_usd=0.0, limit_usd=10.0)
    assert result.usable
    assert result.verdict == "unsubscribe"
    assert not result.converts


def test_a_verdict_whose_quote_is_not_in_the_reply_is_discarded():
    """The failure mode of a classifier over free text is a confident label
    with nothing behind it, and this decides whether somebody is contacted
    again."""
    result = classify(client=FakeClient(
        '{"verdict": "positive", "quote": "I would love a demo next week"}'),
        guard=AllowingGuard(), text=REPLY, consumed_usd=0.0, limit_usd=10.0)
    assert not result.usable
    assert "not in the reply" in result.blocked


def test_whitespace_and_case_do_not_break_a_quote():
    result = classify(client=FakeClient(
        '{"verdict": "unsubscribe", "quote": "Please  Take Me Off Your List"}'),
        guard=AllowingGuard(), text=REPLY, consumed_usd=0.0, limit_usd=10.0)
    assert result.usable


def test_an_empty_reply_is_not_a_verdict():
    """A provider that reports a reply with no body has told us only that a
    human responded. That is not the same as a classification."""
    client = FakeClient('{"verdict": "positive", "quote": ""}')
    result = classify(client=client, guard=AllowingGuard(), text="   ",
                      consumed_usd=0.0, limit_usd=10.0)
    assert not result.usable
    assert "no text" in result.blocked
    assert client.calls == 0, "an empty reply must not cost a model call"


def test_a_refused_spend_blocks_rather_than_guesses():
    client = FakeClient('{"verdict": "positive", "quote": "x"}')
    result = classify(client=client, guard=RefusingGuard(), text=REPLY,
                      consumed_usd=39.99, limit_usd=40.0, allow_downgrade=False)
    assert not result.usable
    assert "spend guard" in result.blocked
    assert client.calls == 0


def test_a_blocked_triage_never_converts():
    blocked = Triage(verdict="positive", quote="x", text=REPLY,
                     spend=SpendVerdict("no", None, None, None), completion=None,
                     model="m", blocked="spend guard: no")
    assert not blocked.usable
    assert not blocked.converts


def test_only_positive_converts():
    for verdict in ("unsubscribe", "negative", "wrong_person", "not_now"):
        result = Triage(verdict=verdict, quote="q", text="q",
                        spend=SpendVerdict("yes", 0.0, None, None),
                        completion=None, model="m")
        assert result.usable and not result.converts
    positive = Triage(verdict="positive", quote="q", text="q",
                      spend=SpendVerdict("yes", 0.0, None, None),
                      completion=None, model="m")
    assert positive.converts


# -- the effect on the runtime -------------------------------------------

@requires_db
def test_an_unsubscribe_in_prose_suppresses(db, tenant):
    """The same path a provider's unsubscribe event takes. Two suppression
    mechanisms would have to agree forever."""
    from runtime.engine import inbound
    from runtime.repo import entities

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        entities.upsert_person(cur, tid, email="ada@northwind.example",
                               consent_state={"email": {"basis": "legitimate_interest"}})
        event = inbound.store(cur, tid, provider="smartlead", signature_ok=True, payload={
            "event_type": "reply", "id": "prose-unsub",
            "lead": {"email": "ada@northwind.example"},
            "reply_message": {"text": REPLY}})
        verdict = Triage(verdict="unsubscribe", quote="take me off your list",
                         text=REPLY, spend=SpendVerdict("yes", 0.0, None, None),
                         completion=None, model="m")
        result = inbound.apply(cur, tid, event, triage=verdict)

    assert "triage.unsubscribe" in result.effects
    with db.tenant_tx(tid) as cur:
        cur.execute("select consent_state from person where email = 'ada@northwind.example'")
        assert cur.fetchone()["consent_state"]["email"]["opted_out"] is True
        cur.execute("select count(*) as n from outcome where type = 'reply_positive'")
        assert cur.fetchone()["n"] == 0, "a removal request is not a conversion"


@requires_db
def test_a_negative_reply_is_recorded_and_does_not_convert(db, tenant):
    from zolts.metrics import DEFAULT as METRIC
    from runtime.engine import inbound

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        event = inbound.store(cur, tid, provider="smartlead", signature_ok=True, payload={
            "event_type": "reply", "id": "neg-1",
            "reply_message": {"text": "We already use a competitor."}})
        verdict = Triage(verdict="negative", quote="already use a competitor",
                         text="We already use a competitor.",
                         spend=SpendVerdict("yes", 0.0, None, None), completion=None,
                         model="m")
        inbound.apply(cur, tid, event, triage=verdict)
        cur.execute("select type from outcome")
        types = [r["type"] for r in cur.fetchall()]

    assert types == ["reply_negative"]
    assert "reply_negative" not in METRIC.events


@requires_db
def test_without_a_usable_verdict_the_behaviour_is_unchanged(db, tenant):
    """Changing this silently would move every tenant's measured lift on a
    deploy. It is a registered decision, not an oversight."""
    from runtime.engine import inbound

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        event = inbound.store(cur, tid, provider="smartlead", signature_ok=True,
                              payload={"event_type": "reply", "id": "no-verdict"})
        inbound.apply(cur, tid, event, triage=None)
        cur.execute("select type from outcome")
        assert [r["type"] for r in cur.fetchall()] == ["reply_positive"]


@requires_db
def test_a_blocked_verdict_is_not_a_weaker_signal(db, tenant):
    """It is no signal, and the runtime keeps the behaviour it had."""
    from runtime.engine import inbound

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        event = inbound.store(cur, tid, provider="smartlead", signature_ok=True,
                              payload={"event_type": "reply", "id": "blocked-verdict"})
        blocked = Triage(verdict="unsubscribe", quote="nope", text="hi",
                         spend=SpendVerdict("no", None, None, None), completion=None,
                         model="m", blocked="spend guard: no")
        inbound.apply(cur, tid, event, triage=blocked)
        cur.execute("select type from outcome")
        assert [r["type"] for r in cur.fetchall()] == ["reply_positive"]


def test_the_reply_body_is_found_wherever_the_provider_puts_it():
    from runtime.engine.inbound import _reply_text

    assert _reply_text({"reply_message": {"text": "hello"}}) == "hello"
    assert _reply_text({"message": {"text": "hi"}}) == "hi"
    assert _reply_text({"body_text": "there"}) == "there"
    assert _reply_text({"lead": {"email": "a@b.c"}}) is None
    assert _reply_text({"reply_message": {"text": "   "}}) is None


@requires_db
def test_the_api_starts_without_the_agent_layer(db, monkeypatch):
    """An inbound webhook must not start failing because a model is
    unavailable, so absent the agent layer the receiver gets nothing and the
    reply lands as it always did."""
    from runtime.api.app import create_app

    monkeypatch.delenv("ZOLTS_AGENTS", raising=False)
    app = create_app(db)
    assert app.state.triage_factory is None


@requires_db
def test_a_reply_lands_even_when_the_classifier_raises(db, tenant, monkeypatch):
    from fastapi.testclient import TestClient

    from runtime.api.app import create_app
    from runtime.provision import create_webhook_endpoint
    from tests.conftest import SECRET

    tid = str(tenant["id"])
    hook = create_webhook_endpoint(db, tid, provider="smartlead", secret_key=SECRET)
    app = create_app(db, secret_key=SECRET)

    def exploding(_tenant_id):
        raise RuntimeError("the model is on fire")

    app.state.triage_factory = exploding
    client = TestClient(app, raise_server_exceptions=False)

    import hashlib
    import hmac
    import json as _json

    body = _json.dumps({"event_type": "reply", "id": "resilient-1",
                        "reply_message": {"text": "please remove me"}}).encode()
    signature = hmac.new(hook["secret"].encode(), body, hashlib.sha256).hexdigest()
    response = client.post(hook["path"].replace("/webhooks/", "/v1/webhooks/"),
                           content=body,
                           headers={"x-smartlead-signature": signature,
                                    "content-type": "application/json"})
    assert response.status_code == 202, response.text

    with db.tenant_tx(tid) as cur:
        cur.execute("select type from outcome")
        assert [r["type"] for r in cur.fetchall()] == ["reply_positive"]
