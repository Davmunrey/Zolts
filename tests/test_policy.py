"""The policy engine must block by default and record a reason every time."""

from datetime import datetime

from zolts.policy import (
    ActionContext,
    Basis,
    Contact,
    Decision,
    evaluate,
)

NOON = ActionContext(channel="email", now=datetime(2026, 9, 4, 12), local_hour=12)


def _ctx(channel: str, **kwargs) -> ActionContext:
    base = {"now": datetime(2026, 9, 4, 12), "local_hour": 12}
    base.update(kwargs)
    return ActionContext(channel=channel, **base)


def test_german_b2b_email_without_consent_is_denied():
    """The declarative case from docs/04. Conservative posture by default."""
    contact = Contact(entity_id="p1", country="DE")
    decision = evaluate(contact, _ctx("email"))
    assert decision.decision is Decision.DENY
    assert decision.rule_key == "jurisdiction.DE.basis_required"


def test_spanish_b2b_email_under_legitimate_interest_is_allowed():
    contact = Contact(entity_id="p1", country="ES",
                      consent={"email": Basis.LEGITIMATE_INTEREST})
    assert evaluate(contact, _ctx("email")).allowed


def test_unknown_jurisdiction_defaults_to_requiring_consent():
    """An unmapped country is never an implicit allow."""
    contact = Contact(entity_id="p1", country="ZZ",
                      consent={"email": Basis.LEGITIMATE_INTEREST})
    decision = evaluate(contact, _ctx("email"))
    assert decision.decision is Decision.DENY
    assert decision.jurisdiction == "__unknown__"


def test_consent_satisfies_a_legitimate_interest_requirement():
    contact = Contact(entity_id="p1", country="ES", consent={"email": Basis.CONSENT})
    assert evaluate(contact, _ctx("email")).allowed


def test_legitimate_interest_never_substitutes_for_consent():
    contact = Contact(entity_id="p1", country="ES",
                      consent={"whatsapp": Basis.LEGITIMATE_INTEREST})
    assert evaluate(contact, _ctx("whatsapp")).decision is Decision.DENY


def test_unsubscribe_beats_a_valid_legal_basis():
    contact = Contact(entity_id="p1", country="ES",
                      consent={"email": Basis.CONSENT},
                      unsubscribed_channels=frozenset({"email"}))
    decision = evaluate(contact, _ctx("email"))
    assert decision.decision is Decision.DENY
    assert decision.rule_key == "suppression.unsubscribed"


def test_unsubscribe_beats_a_customer_override():
    """Overrides let a customer accept legal risk, never override a person's opt-out."""
    contact = Contact(entity_id="p1", country="DE",
                      unsubscribed_channels=frozenset({"email"}))
    decision = evaluate(contact, _ctx("email", overrides=frozenset({"basis.email"})))
    assert decision.rule_key == "suppression.unsubscribed"


def test_national_suppression_list_blocks_voice():
    contact = Contact(entity_id="p1", country="ES",
                      consent={"voice": Basis.LEGITIMATE_INTEREST},
                      suppressed_on=frozenset({"robinson_list_es"}))
    assert evaluate(contact, _ctx("voice")).rule_key == "suppression.robinson_list_es"


def test_blocked_channel_requires_a_recorded_override():
    contact = Contact(entity_id="p1", country="DE", consent={"voice": Basis.CONSENT})
    assert evaluate(contact, _ctx("voice")).rule_key == "jurisdiction.DE.channel_blocked"
    allowed = evaluate(contact, _ctx("voice", overrides=frozenset({"channel.voice"})))
    assert allowed.allowed


def test_override_of_legal_basis_routes_to_review_not_allow():
    """A customer override buys human review, not silent execution."""
    contact = Contact(entity_id="p1", country="DE")
    decision = evaluate(contact, _ctx("email", overrides=frozenset({"basis.email"})))
    assert decision.decision is Decision.REVIEW


def test_frequency_cap_is_enforced():
    contact = Contact(entity_id="p1", country="ES",
                      consent={"email": Basis.LEGITIMATE_INTEREST},
                      touches_this_week=3)
    assert evaluate(contact, _ctx("email")).rule_key == "frequency.cap_reached"


def test_quiet_hours_block_voice_but_not_email():
    contact = Contact(entity_id="p1", country="ES",
                      consent={"voice": Basis.LEGITIMATE_INTEREST,
                               "email": Basis.LEGITIMATE_INTEREST})
    assert evaluate(contact, _ctx("voice", local_hour=22)).rule_key == "quiet_hours"
    assert evaluate(contact, _ctx("email", local_hour=22)).allowed


def test_budget_ceiling_blocks_the_action():
    contact = Contact(entity_id="p1", country="ES",
                      consent={"email": Basis.LEGITIMATE_INTEREST})
    decision = evaluate(contact, _ctx("email", remaining_budget_eur=0.01, action_cost_eur=0.05))
    assert decision.rule_key == "budget.exceeded"


def test_every_decision_carries_a_rule_key_and_rationale():
    """Auditability is the product claim: no decision may be unexplained."""
    cases = [
        Contact(entity_id="a", country="DE"),
        Contact(entity_id="b", country="ES", consent={"email": Basis.CONSENT}),
        Contact(entity_id="c", country="ZZ"),
    ]
    for contact in cases:
        decision = evaluate(contact, _ctx("email"))
        assert decision.rule_key and decision.rationale
