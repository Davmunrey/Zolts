"""Claim provenance. What a generated message is allowed to assert.

The tests that matter are the ones about what gets removed. A generated
message that reads well and contains an invented figure is the failure mode
this check exists for, and it is invisible to anyone reading the output.
"""

from __future__ import annotations

import pytest

from zolts.provenance import Evidence, Source, split_claims, verify

DOSSIER = [
    Evidence(Source.RETRIEVED, "Northwind raised a 12 million dollar Series A in March 2026.",
             "crunchbase#1"),
    Evidence(Source.RETRIEVED, "Northwind opened four RevOps roles last quarter.", "linkedin#2"),
    Evidence(Source.CRM, "Account owner is Dana Cruz, economic buyer.", "crm.contact"),
    Evidence(Source.PROOF, "Kestrel cut onboarding time by 40 percent with our platform.",
             "proof.kestrel"),
]


def test_an_unsupported_claim_is_removed():
    verdict = verify("Northwind opened four RevOps roles last quarter. "
                     "You are the market leader in logistics.", DOSSIER)
    assert "four RevOps roles" in verdict.message
    assert "market leader" not in verdict.message
    assert verdict.dropped


def test_an_invented_figure_flags_a_human():
    """A dropped number usually means the model made one up."""
    verdict = verify("Northwind raised a 12 million dollar Series A in March 2026. "
                     "Our customers see a 340 percent return within six weeks.", DOSSIER)
    assert verdict.needs_human
    assert "quantified" in verdict.reason


def test_greetings_and_questions_need_no_provenance():
    """Demanding a citation for 'Hi Dana' empties every message and teaches
    operators to waive the check."""
    verdict = verify("Hi Dana. Northwind opened four RevOps roles last quarter. "
                     "Would you be open to a short call?", DOSSIER)
    assert verdict.needs_human is False
    assert "Hi Dana." in verdict.message
    assert "short call" in verdict.message


def test_a_message_with_nothing_left_is_not_a_message():
    verdict = verify("Hi Dana. You are the undisputed leader in your category. "
                     "Worth a chat?", DOSSIER)
    assert verdict.needs_human
    assert "asserts nothing" in verdict.reason


def test_factuality_is_the_share_of_claims_with_provenance():
    verdict = verify("Northwind opened four RevOps roles last quarter. "
                     "Northwind raised a 12 million dollar Series A in March 2026. "
                     "You are the market leader.", DOSSIER)
    assert verdict.factuality == pytest.approx(2 / 3, abs=0.01)


def test_evidence_from_any_of_the_three_sources_counts():
    for evidence in DOSSIER:
        verdict = verify(evidence.text, [evidence])
        assert not verdict.dropped, f"{evidence.source} evidence must support its own text"


def test_a_long_document_does_not_support_everything():
    """Overlap is measured over the sentence, not the evidence.

    Measured the other way, a large enough document supports any sentence by
    surface area alone — which is how a provenance check becomes decorative.
    """
    haystack = [Evidence(Source.RETRIEVED, " ".join(
        ["revenue growth pipeline logistics platform onboarding customers europe"] * 200),
        "big#1")]
    verdict = verify("Northwind raised a 12 million dollar Series A.", haystack)
    assert verdict.dropped


def test_claims_split_on_sentence_boundaries():
    assert len(split_claims("One. Two! Three?\nFour")) == 4


def test_an_interpretive_clause_does_not_sink_a_cited_fact():
    """Regression, and the reason the rule measures anchors rather than words.

    The first version dropped this exact sentence with its exact fact in
    evidence: the interpretive clause diluted the token overlap below the
    threshold. A provenance check that deletes the writing and keeps nothing
    is not a check, it is an outage.
    """
    verdict = verify("Northwind opened four RevOps roles last quarter, which usually means "
                     "the reporting layer is about to be rebuilt.", DOSSIER)
    assert verdict.dropped == []
    assert "reporting layer" in verdict.message


def test_an_opinion_needs_no_source_but_an_absolute_does():
    """'This usually means X' is the seller's reading. 'You are the leader' is
    a claim about the world."""
    opinion = verify("Teams in that position usually rebuild reporting first.", DOSSIER)
    absolute = verify("You are the leader in that category.", DOSSIER)
    assert opinion.dropped == []
    assert absolute.dropped


def test_a_sentence_mixing_a_real_figure_with_an_invented_one_is_dropped():
    """Not partly true. A rule that accepted it would wave through exactly the
    messages this check exists to stop."""
    verdict = verify("Northwind raised a 12 million dollar Series A and grew headcount "
                     "by 815 percent.", DOSSIER)
    assert verdict.dropped and verdict.needs_human
