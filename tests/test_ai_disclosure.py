"""The AI-disclosure marker, from the pack to the draft.

`zolts.evals.compliance_checks` has refused a draft carrying no disclosure
marker since the agent layer shipped, and until now nothing ever asked it to:
`requires_ai_disclosure` was a keyword defaulting to `False` on `generate.run`
and `copywriter.draft` with no caller that set it (D-90). `docs/11` listed AI
content disclosure as *configurable per jurisdiction and channel*, which is the
repository's dominant defect shape landing in the AI Act section.

The answer belongs in the published pack, not in code. The EU AI Act's
transparency obligation is territorial, and ADR-044 built packs so a regulatory
change ships without a deployment — so which jurisdictions require the marker is
a document counsel can republish, and decision 55 records the default this
release ships with.

These tests follow the value the whole way: the rule carries it, the canonical
form hashes it, an override may add it and may never drop it, the gate resolves
it from the *published* pack, and the worker hands the gate's answer to the
generator rather than resolving it a second time.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.conftest import requires_db
from zolts import policy

ROOT = Path(__file__).resolve().parent.parent

# The jurisdictions the shipped pack answers for, and whether decision 55's
# default requires the marker there. GB is deliberately false: the AI Act is EU
# law and the United Kingdom left.
EXPECTED = {"ES": True, "DE": True, "FR": True, "GB": False, "US": False, "CA": False}


def test_the_shipped_pack_answers_for_every_country_it_carries():
    """The premise. A pack that gained a country nobody classified would leave
    the marker off there by the dataclass default, silently."""
    assert set(policy.PACK_V1) == set(EXPECTED), (
        f"the shipped pack covers {sorted(policy.PACK_V1)} and this file "
        f"classifies {sorted(EXPECTED)}")


@pytest.mark.parametrize("country,required", sorted(EXPECTED.items()))
def test_the_pack_carries_decision_55s_default(country, required):
    assert policy.disclosure_required(country, policy.PACK_V1) is required


def test_an_unknown_jurisdiction_requires_the_marker():
    """Fail-closed, the same direction as consent. The marker costs a sentence;
    its absence where the law wanted one is a breach."""
    assert policy.disclosure_required("ZZ", policy.PACK_V1) is True
    assert policy.UNKNOWN_JURISDICTION.ai_disclosure is True


def test_the_digest_covers_the_field():
    """A document that omitted it would hash the same across a change to it,
    and the digest would certify a rule it never covered — the reason
    `to_document` names every field a rule decides by."""
    import dataclasses

    relaxed = dict(policy.PACK_V1)
    relaxed["ES"] = dataclasses.replace(relaxed["ES"], ai_disclosure=False)
    assert policy.pack_digest(relaxed) != policy.pack_digest(policy.PACK_V1)
    assert "ai_disclosure" in policy.to_document(policy.PACK_V1)["rules"]["ES"]


def test_a_pack_survives_a_round_trip_through_its_document():
    assert policy.from_document(policy.to_document(policy.PACK_V1)) == policy.PACK_V1


def test_a_document_written_before_the_field_existed_means_not_required():
    """Absent is false, not true. Reading an old document's silence as
    *required* would change what a pack somebody published last year meant, and
    a decision citing its digest would no longer describe what happened."""
    document = policy.to_document(policy.PACK_V1)
    for rule in document["rules"].values():
        rule.pop("ai_disclosure")
    restored = policy.from_document(document)
    assert all(not rule.ai_disclosure for rule in restored.values())


def test_a_programme_may_demand_the_marker_where_the_jurisdiction_does_not():
    """Overrides tighten. The mechanism is `replace`, so a programme that says
    nothing about disclosure inherits the pack's answer rather than clearing
    it — which is the failure that would matter, and it is asserted below."""
    assert policy.disclosure_required("US", policy.PACK_V1) is False
    tightened = policy.tighten(policy.rule_for("US", policy.PACK_V1),
                               {"lists_check": ["dnc_us"]})
    assert tightened.suppression_lists == ("dnc_us",)


def test_an_override_cannot_drop_the_marker_by_omission():
    """The direction that matters. A programme's policy block names quiet hours
    and suppression lists and says nothing about disclosure; the jurisdiction's
    answer must survive that silence."""
    overrides = {"quiet_hours": {"start": "21:00", "end": "09:00"},
                 "lists_check": ["robinson_list_es"]}
    assert policy.disclosure_required("ES", policy.PACK_V1, overrides) is True


def test_the_gate_hands_the_answer_to_the_generator_rather_than_resolving_it_twice():
    """The call site, pinned. Every test above passes with `generate.run` still
    taking the default, so the wiring needs its own assertion — the same gap
    that left the check switched off for the whole life of the agent layer.
    """
    worker = ast.parse((ROOT / "runtime" / "engine" / "worker.py").read_text())
    calls = [node for node in ast.walk(worker)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "run"
             and isinstance(node.func.value, ast.Name) and node.func.value.id == "generate"]
    assert calls, "the worker no longer calls generate.run"
    for call in calls:
        passed = {kw.arg: kw.value for kw in call.keywords}
        assert "requires_ai_disclosure" in passed, (
            "generate.run is called without the disclosure flag, so it takes the "
            "default and the check is off again (D-90)")
        value = passed["requires_ai_disclosure"]
        assert isinstance(value, ast.Attribute) and value.attr == "requires_ai_disclosure", (
            "the flag is passed as something other than the gate's answer")


def test_the_gate_result_defaults_the_flag_off():
    """A `GateResult` built without it is a gate that did not answer, and the
    safe reading of *did not answer* here is the pack's own silence, not a
    marker demanded of every message in every market."""
    from runtime.engine.gate import GateResult

    result = GateResult(allowed=True, decision="allow", rule_key="ok",
                        rationale="", jurisdiction="US", decision_id="x")
    assert result.requires_ai_disclosure is False


# ── on the live path, against the published pack ──────────────────────────

@requires_db
@pytest.mark.parametrize("country,required", [("ES", True), ("US", False), ("ZZ", True)])
def test_the_gate_resolves_the_marker_from_the_published_pack(db, tenant, country, required):
    """Against a real database and whatever pack is active in it, because that
    is the difference the wiring makes: `policy.PACK_V1` is the dict this
    release ships with, and the gate must answer from the row an operator can
    republish (ADR-044).

    The premise is asserted first: the active pack has to be the shipped one
    for its answers to be the ones this file classified. A deployment running
    an operator's own pack is a different question and a different test.
    """
    import uuid

    from runtime import policy_packs
    from runtime.engine import gate
    from runtime.repo import entities

    with db.admin_tx() as cur:
        active = policy_packs.active(cur)
    assert active["digest"] == policy.pack_digest(policy.PACK_V1), (
        "the active pack is not the shipped one, so its answers are not the "
        "ones this test classified")

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        person = entities.upsert_person(
            cur, tid, email=f"d-{uuid.uuid4().hex[:8]}@example.com", full_name="D",
            country=country,
            consent_state={"email": {"basis": "consent", "source": "test"}})
        result = gate.check(cur, tid, person=dict(person), channel="email",
                            enrollment_id=None, program_spec={})

    assert result.requires_ai_disclosure is required
