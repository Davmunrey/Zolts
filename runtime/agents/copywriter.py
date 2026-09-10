"""The copywriter: the first agent, and the shape every other one takes.

It drafts one message and stops. It does not send, does not queue, does not
decide whether it is good enough — it returns a proposal, and the runtime
decides. That separation is ADR-004, and it is the reason a regulated buyer
can be shown this code.

The order of operations is the design:

1. Assemble the dossier by retrieval, not by dumping the CRM into the prompt.
   Dumping is expensive, noisy, and an unnecessary PII exposure.
2. Count the tokens the call would actually send.
3. Ask the spend guard whether the call may be made. A refusal names a cheaper
   model, and the caller may retry on it — that is the point of the refusal
   carrying a lever.
4. Generate.
5. Strip every claim nothing supports.
6. Score the result and let the gate decide who sends it.

Steps 3 and 5 are the ones that make it a product rather than a wrapper.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from runtime.agents.client import Completion, ModelClient, ModelUnavailable
from runtime.agents.spend import SpendGuard, SpendVerdict
from zolts import evals, offers, provenance

log = logging.getLogger("zolts.agents.copywriter")

PROMPT_VERSION = "copywriter/2026-09-06"

SYSTEM = """\
You write one short outbound email for a B2B seller. Rules, in order of importance:

1. Every factual statement you make must come from the EVIDENCE block. If the
   evidence does not support a sentence, do not write that sentence. Do not
   estimate, extrapolate or round a figure that is not there.
2. No superlatives, no marketing register, no "revolutionary", "seamless",
   "world-class", "game-changing". A buyer skips those and so should you.
3. Lead with the observed trigger and what it usually implies for the reader's
   work. One proof point, at most.
4. End with a low-friction ask and the opt-out instruction you are given.
5. Between 60 and 150 words. Plain sentences. No placeholders of any kind:
   every name you use must be in the evidence.

Return only the message body. No subject line, no commentary."""


@dataclass
class Draft:
    """What the copywriter hands back. It is a proposal, not a send."""
    text: str
    raw_text: str
    evidence: list[provenance.Evidence]
    verification: provenance.Verdict
    evaluation: evals.EvalResult
    gate: evals.Gate
    spend: SpendVerdict
    completion: Completion | None
    model: str
    cost_micros: int
    prompt_version: str = PROMPT_VERSION
    blocked: str | None = None
    over_authority: list["offers.Offer"] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def state(self) -> str:
        """Where this proposal lands in the review queue."""
        if self.blocked:
            return "rejected"
        if self.gate.auto_send:
            return "approved"
        return "needs_human"

    def as_content(self) -> dict[str, Any]:
        return {"body": self.text, "raw": self.raw_text,
                "dropped_claims": self.verification.dropped,
                # The offers a reviewer has to decide about, kept beside the
                # dropped claims: both are reasons this message stopped, and a
                # reviewer who sees one and not the other is guessing.
                "unauthorised_offers": [o.sentence for o in self.over_authority],
                "needs_human_reason": self.verification.reason}


def _evidence_block(evidence: list[provenance.Evidence]) -> str:
    return "\n".join(f"[{item.ref}] ({item.source.value}) {item.text}" for item in evidence)


def draft(*, client: ModelClient, guard: SpendGuard, evidence: list[provenance.Evidence],
          tier: str, opt_out: str, policy_allows: bool, tenant_enabled: bool,
          consumed_usd: float | None, limit_usd: float | None,
          threshold: float | None = None, requires_ai_disclosure: bool = False,
          prohibited: tuple[str, ...] = (), label: str = "copywriter",
          allow_downgrade: bool = True,
          authority: "offers.Authority | None" = None) -> Draft:
    """Draft one message, priced and gated. Never sends."""
    user = (f"EVIDENCE\n{_evidence_block(evidence)}\n\n"
            f"OPT-OUT INSTRUCTION TO INCLUDE VERBATIM\n{opt_out}\n\n"
            "Write the message.")
    messages = [{"role": "user", "content": user}]

    def _empty(reason: str, spend: SpendVerdict, model: str) -> Draft:
        verification = provenance.Verdict()
        evaluation = evals.EvalResult()
        return Draft(text="", raw_text="", evidence=evidence, verification=verification,
                     evaluation=evaluation,
                     gate=evals.Gate(False, reason, None, threshold),
                     spend=spend, completion=None, model=model, cost_micros=0,
                     blocked=reason)

    model = client.model
    try:
        input_tokens = client.count_tokens(system=SYSTEM, messages=messages, model=model)
    except (ModelUnavailable, Exception) as exc:  # noqa: BLE001 - counting must not send
        return _empty(f"could not price the call: {exc}",
                      SpendVerdict("cannot-tell", None, str(exc), None), model)

    verdict = guard.check(model=model, input_tokens=input_tokens, output_tokens=400,
                          consumed_usd=consumed_usd, limit_usd=limit_usd, label=label)
    if not verdict.allowed and allow_downgrade:
        # The refusal carried a lever. Taking it is the whole reason the guard
        # answers with alternatives instead of a bare no.
        alternative = verdict.cheapest_alternative
        if alternative and alternative.model:
            model = alternative.model
            input_tokens = client.count_tokens(system=SYSTEM, messages=messages, model=model)
            verdict = guard.check(model=model, input_tokens=input_tokens, output_tokens=400,
                                  consumed_usd=consumed_usd, limit_usd=limit_usd, label=label)
            log.info("spend guard refused %s, retrying on %s", client.model, model)
    if not verdict.allowed:
        return _empty(f"spend guard: {verdict.verdict}"
                      + (f" ({verdict.reason})" if verdict.reason else ""), verdict, model)

    try:
        completion = client.complete(system=SYSTEM, messages=messages, model=model)
    except ModelUnavailable as exc:
        return _empty(f"model unavailable: {exc}", verdict, model)

    cost_micros = verdict.estimated_micros
    if completion.refused:
        draft_result = _empty("the model declined to generate this message", verdict, model)
        draft_result.completion = completion
        draft_result.cost_micros = cost_micros
        return draft_result

    verification = provenance.verify(completion.text, evidence)
    evaluation = evals.evaluate(
        verification.message, factuality=verification.factuality,
        requires_ai_disclosure=requires_ai_disclosure, prohibited=prohibited)
    decision = evals.gate(evaluation, tier=tier, policy_allows=policy_allows,
                          tenant_enabled=tenant_enabled, threshold=threshold)
    if verification.needs_human and decision.auto_send:
        # Provenance overrides a passing score. A message can read perfectly
        # and still have had a number removed from it.
        decision = evals.Gate(False, f"provenance: {verification.reason}",
                              decision.score, decision.threshold)
    # And the archetype's commercial latitude overrides both. Provenance asks
    # whether a claim is true; this asks whether we are allowed to say it, and
    # no amount of evidence makes an unauthorised offer permissible (D-85).
    # Never dropped like an unsupported claim: deleting the offer and sending
    # the rest is precisely the case `provenance` sends to a person instead.
    over = offers.exceeding(verification.message, authority)
    if over and decision.auto_send:
        decision = evals.Gate(False, f"discount authority: {offers.reason(over, authority)}",
                              decision.score, decision.threshold)

    return Draft(text=verification.message, raw_text=completion.text, evidence=evidence,
                 verification=verification, evaluation=evaluation, gate=decision,
                 spend=verdict, completion=completion, model=model,
                 cost_micros=cost_micros, over_authority=over,
                 meta={"input_tokens": completion.input_tokens,
                       "output_tokens": completion.output_tokens,
                       "downgraded": model != client.model,
                       "routed": client.routed})
