"""Reading what a reply actually says.

Until now every reply was recorded as `reply_positive`. A person writing "take
me off your list" was counted as a conversion, left contactable, and folded into
the lift the product reports — which makes the primary metric wrong in the one
direction a measurement product cannot afford, and leaves an opt-out expressed
in prose invisible to the compliance layer. Only a provider-signalled
unsubscribe ever suppressed anybody.

The agent classifies the text and stops there. It does not suppress, does not
record an outcome and does not decide whether it is confident enough — it
returns a verdict with the words it relied on, and the runtime decides. That is
product invariant 1, and it is why an unsubscribe found here goes through the
same suppression path a provider's unsubscribe event does rather than a new one.

**A classification must quote.** The verdict carries the span of the reply that
supports it, and a verdict whose quote is not in the reply is discarded: the
failure mode of a classifier over free text is a confident label with nothing
behind it, and a label that cannot point at the words is exactly that.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from runtime.agents.client import Completion, ModelClient, ModelUnavailable
from runtime.agents.spend import SpendGuard, SpendVerdict

log = logging.getLogger(__name__)

PROMPT_VERSION = "triage-v1"

# Ordered by what being wrong costs. An unsubscribe missed is a compliance
# failure; a positive missed is an under-count, which this product prefers.
VERDICTS = ("unsubscribe", "negative", "wrong_person", "not_now", "positive")

# Counted as a conversion by the console. Everything else is recorded and does
# not move the measured lift.
CONVERTING = frozenset({"positive"})

SYSTEM = """You classify replies to cold business outreach. You do not write \
replies, take actions, or make recommendations.

Return JSON only, with exactly these keys:
  "verdict": one of unsubscribe, negative, wrong_person, not_now, positive
  "quote":   the shortest span copied verbatim from the reply that supports it

Definitions:
  unsubscribe  - asks not to be contacted again, in any wording
  negative     - declines, is not interested, or objects
  wrong_person - says they are not the right contact, or has left the company
  not_now      - open in principle but asks to be contacted later
  positive     - asks a question, requests a call, or expresses interest

Rules:
- An unsubscribe outranks everything else in the same message. If someone asks \
a question and also asks to be removed, the verdict is unsubscribe.
- The quote must appear character for character in the reply. Never paraphrase \
it, never translate it, never repair its spelling.
- If the reply is an auto-reply, a bounce notice or otherwise says nothing \
about the person's intent, use not_now."""


@dataclass
class Triage:
    """What the triage agent hands back. It is a reading, not an action."""
    verdict: str
    quote: str
    text: str
    spend: SpendVerdict
    completion: Completion | None
    model: str
    cost_micros: int = 0
    prompt_version: str = PROMPT_VERSION
    blocked: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        """Whether the runtime may act on this.

        A blocked or unquoted verdict is not a weaker signal to be used with
        caution; it is no signal, and the caller keeps the behaviour it had.
        """
        return self.blocked is None and self.verdict in VERDICTS

    @property
    def converts(self) -> bool:
        return self.usable and self.verdict in CONVERTING

    def as_content(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "quote": self.quote,
                "reply": self.text[:2000], "blocked": self.blocked}


def _normalise_for_quoting(value: str) -> str:
    """Whitespace and case are not what a quote is for.

    A model that returns the right words with a collapsed newline has quoted
    correctly; one that returns words the reply does not contain has not.
    """
    return re.sub(r"\s+", " ", value).strip().lower()


def parse(raw: str) -> tuple[str | None, str, str | None]:
    """Read a verdict out of a completion. Returns (verdict, quote, error)."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, "", f"not JSON: {exc}"
    if not isinstance(payload, dict):
        return None, "", "not a JSON object"
    verdict = str(payload.get("verdict", "")).strip().lower()
    quote = str(payload.get("quote", "") or "")
    if verdict not in VERDICTS:
        return None, quote, f"unknown verdict {verdict!r}; expected one of {', '.join(VERDICTS)}"
    return verdict, quote, None


def classify(*, client: ModelClient, guard: SpendGuard, text: str,
             consumed_usd: float | None, limit_usd: float | None,
             label: str = "triage", allow_downgrade: bool = True) -> Triage:
    """Read one reply. Never suppresses, never records, never sends."""
    stripped = (text or "").strip()

    def _blocked(reason: str, spend: SpendVerdict, model: str, quote: str = "") -> Triage:
        return Triage(verdict="", quote=quote, text=stripped, spend=spend,
                      completion=None, model=model, blocked=reason)

    empty_spend = SpendVerdict("cannot-tell", None, "not called", None)
    if not stripped:
        # Nothing to read. The caller keeps whatever it did before, which is
        # not the same as a verdict of "positive".
        return _blocked("the reply carries no text", empty_spend, client.model)

    messages = [{"role": "user", "content": f"REPLY\n{stripped}\n\nClassify it."}]
    model = client.model
    try:
        input_tokens = client.count_tokens(system=SYSTEM, messages=messages, model=model)
    except Exception as exc:  # noqa: BLE001 - counting must not send
        return _blocked(f"could not price the call: {exc}",
                        SpendVerdict("cannot-tell", None, str(exc), None), model)

    verdict = guard.check(model=model, input_tokens=input_tokens, output_tokens=120,
                          consumed_usd=consumed_usd, limit_usd=limit_usd, label=label)
    if not verdict.allowed and allow_downgrade:
        alternative = verdict.cheapest_alternative
        if alternative and alternative.model:
            model = alternative.model
            input_tokens = client.count_tokens(system=SYSTEM, messages=messages, model=model)
            verdict = guard.check(model=model, input_tokens=input_tokens, output_tokens=120,
                                  consumed_usd=consumed_usd, limit_usd=limit_usd, label=label)
            log.info("spend guard refused %s for triage, retrying on %s", client.model, model)
    if not verdict.allowed:
        return _blocked(f"spend guard: {verdict.verdict}"
                        + (f" ({verdict.reason})" if verdict.reason else ""), verdict, model)

    try:
        completion = client.complete(system=SYSTEM, messages=messages, model=model)
    except ModelUnavailable as exc:
        return _blocked(f"model unavailable: {exc}", verdict, model)

    parsed, quote, error = parse(completion.text)
    if error or parsed is None:
        return _blocked(f"unreadable classification: {error}", verdict, model, quote)

    if _normalise_for_quoting(quote) not in _normalise_for_quoting(stripped):
        # The one check that makes the verdict answerable. Without it a
        # confident label with nothing behind it reads exactly like a correct
        # one, and this decides whether somebody is contacted again.
        return _blocked(
            "the quote supporting the verdict is not in the reply", verdict, model, quote)

    return Triage(verdict=parsed, quote=quote, text=stripped, spend=verdict,
                  completion=completion, model=model,
                  cost_micros=getattr(completion, "cost_micros", 0) or 0)
