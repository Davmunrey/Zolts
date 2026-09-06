"""The researcher: the account, not the message.

`docs/08` gives this agent a row of its own — account plus signals in, a
dossier with citations and sources out — and `docs/12` prices it at 20
credits, the most expensive action in the list. Nothing produced one. The word
"dossier" existed in this runtime as a local variable inside the copywriter,
holding evidence assembled for one email and discarded when the email was
written.

The shape is the copywriter's, deliberately, because the controls are what
make either one a product rather than a wrapper: count the tokens, ask the
spend guard, generate, then strip every claim nothing supports. What differs
is the subject and the standard.

**A dossier is read by a person, so its failure mode is different.** A message
that loses a sentence to the verifier is a weaker message; a dossier that
loses one is a research document with a hole in it, and the hole is the
finding. Dropped claims are therefore kept and returned rather than counted:
what the model wanted to say and could not support is the most useful thing on
the page for the operator deciding whether to trust it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from runtime.agents.client import Completion, ModelClient, ModelUnavailable
from runtime.agents.spend import SpendGuard, SpendVerdict
from zolts import provenance

log = logging.getLogger("zolts.agents.researcher")

PROMPT_VERSION = "researcher/2026-09-06"

# How much a dossier is allowed to cost in output tokens. A research document
# that runs to two pages is a document nobody reads before a call, which is the
# only moment it is worth anything.
MAX_OUTPUT_TOKENS = 700

SYSTEM = """\
You write a short research brief on one company for a B2B seller preparing to \
contact it. Rules, in order of importance:

1. Every factual statement must come from the EVIDENCE block. If the evidence \
does not support a sentence, do not write that sentence. Never estimate, \
extrapolate, round, or supply a figure, date, name or number that is not there.
2. Write four short paragraphs, in this order and with no headings: what the \
company is; what has happened recently and when; who to approach and why them; \
what to say first.
3. Interpretation is welcome and must be visibly interpretation — "this \
usually means", "worth checking whether". Never state a reading as a fact.
4. No superlatives and no marketing register. This is read by someone \
deciding how to spend twenty minutes.
5. Under 220 words. Plain sentences. No placeholders and no bracketed \
instructions of any kind.

Return only the brief. No preamble, no headings, no bullet list."""


@dataclass
class Dossier:
    """What the researcher hands back. It is a document, never an action."""
    body: str
    raw_body: str
    evidence: list[provenance.Evidence]
    verification: provenance.Verdict
    spend: SpendVerdict
    model: str
    cost_micros: int
    completion: Completion | None = None
    prompt_version: str = PROMPT_VERSION
    refused: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def state(self) -> str:
        """`complete`, `thin`, or `refused`.

        Thin is not a failure and is not hidden. A dossier whose claims were
        mostly struck out is telling the operator that this account is one the
        runtime knows almost nothing about, which is worth more than a
        confident paragraph would have been.
        """
        if self.refused:
            return "refused"
        return "thin" if self.verification.needs_human or self.verification.dropped else "complete"

    def as_row(self) -> dict[str, Any]:
        return {
            "state": self.state, "body": self.body,
            "evidence": [{"source": e.source.value, "text": e.text, "ref": e.ref}
                         for e in self.evidence],
            "dropped": list(self.verification.dropped),
            "model": self.model, "prompt_version": self.prompt_version,
            "cost_micros": self.cost_micros,
        }


def _evidence_block(evidence: list[provenance.Evidence]) -> str:
    return "\n".join(f"[{item.ref}] ({item.source.value}) {item.text}" for item in evidence)


def research(*, client: ModelClient, guard: SpendGuard,
             evidence: list[provenance.Evidence],
             consumed_usd: float | None, limit_usd: float | None,
             label: str = "researcher", allow_downgrade: bool = True) -> Dossier:
    """Write one dossier, priced and verified. Produces no action of any kind."""

    def _refused(reason: str, spend: SpendVerdict, model: str) -> Dossier:
        return Dossier(body="", raw_body="", evidence=evidence,
                       verification=provenance.Verdict(), spend=spend, model=model,
                       cost_micros=0, refused=reason)

    if not evidence:
        # A brief with no evidence cannot carry a claim, so it cannot be
        # written. Refused before the call rather than after: a model asked to
        # research nothing produces the exact document this whole layer exists
        # to prevent.
        return _refused("no evidence: nothing is known about this account yet",
                        SpendVerdict("not-asked", None, "no evidence", None), client.model)

    messages = [{"role": "user",
                 "content": f"EVIDENCE\n{_evidence_block(evidence)}\n\nWrite the brief."}]
    model = client.model
    try:
        input_tokens = client.count_tokens(system=SYSTEM, messages=messages, model=model)
    except Exception as exc:  # noqa: BLE001 - counting must never send
        return _refused(f"could not price the call: {exc}",
                        SpendVerdict("cannot-tell", None, str(exc), None), model)

    verdict = guard.check(model=model, input_tokens=input_tokens,
                          output_tokens=MAX_OUTPUT_TOKENS, consumed_usd=consumed_usd,
                          limit_usd=limit_usd, label=label)
    if not verdict.allowed and allow_downgrade:
        alternative = verdict.cheapest_alternative
        if alternative and alternative.model:
            model = alternative.model
            input_tokens = client.count_tokens(system=SYSTEM, messages=messages, model=model)
            verdict = guard.check(model=model, input_tokens=input_tokens,
                                  output_tokens=MAX_OUTPUT_TOKENS,
                                  consumed_usd=consumed_usd, limit_usd=limit_usd, label=label)
            log.info("spend guard refused %s, retrying on %s", client.model, model)
    if not verdict.allowed:
        return _refused(f"spend guard: {verdict.verdict}"
                        + (f" ({verdict.reason})" if verdict.reason else ""), verdict, model)

    try:
        completion = client.complete(system=SYSTEM, messages=messages, model=model)
    except ModelUnavailable as exc:
        return _refused(f"model unavailable: {exc}", verdict, model)

    cost_micros = verdict.estimated_micros
    if completion.refused:
        result = _refused("the model declined to write this brief", verdict, model)
        result.completion, result.cost_micros = completion, cost_micros
        return result

    verification = provenance.verify(completion.text, evidence)
    return Dossier(body=verification.message, raw_body=completion.text, evidence=evidence,
                   verification=verification, spend=verdict, model=model,
                   cost_micros=cost_micros, completion=completion,
                   meta={"input_tokens": completion.input_tokens,
                         "output_tokens": completion.output_tokens,
                         "downgraded": model != client.model,
                         "routed": client.routed})
