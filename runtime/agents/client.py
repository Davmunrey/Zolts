"""The model client.

One SDK, one shape. `base_url` is configurable so a tenant can route through a
gateway — OmniRoute serves the Messages API shape, so the official Anthropic
SDK reaches it unchanged — without this repository growing a second, provider
neutral abstraction that has to be kept honest.

Every call is priced before it happens and recorded after it. The token count
that the spend guard judges comes from `count_tokens`, not from an estimate:
guessing the input size of the call you are about to authorise defeats the
point of authorising it.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import anthropic

log = logging.getLogger("zolts.agents")

# docs/02 fixes Claude as the primary model. The cheap tier is named here
# rather than chosen per call site so that a cost decision is one edit.
DEFAULT_MODEL = os.environ.get("ZOLTS_MODEL", "claude-opus-5")
CHEAP_MODEL = os.environ.get("ZOLTS_MODEL_CHEAP", "claude-haiku-4-5")

# Refusals arrive as a 200 with stop_reason "refusal". Server-side fallback
# routes them by category so a declined generation degrades to a different
# model rather than to a hole in a sequence.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ModelUnavailable(RuntimeError):
    pass


@dataclass
class Completion:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None
    refused: bool = False
    raw: Any = None
    cost_micros: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


class ModelClient:
    def __init__(self, *, api_key: str | None = None, base_url: str | None = None,
                 model: str = DEFAULT_MODEL) -> None:
        self.model = model
        self.base_url = base_url or os.environ.get("ZOLTS_MODEL_BASE_URL")
        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if self.base_url:
            # A tenant routing through their own gateway. The SDK is the same;
            # only the endpoint moves.
            kwargs["base_url"] = self.base_url
        self._client = anthropic.Anthropic(**kwargs)

    @property
    def routed(self) -> bool:
        return bool(self.base_url)

    def count_tokens(self, *, system: str, messages: list[dict[str, Any]],
                     model: str | None = None) -> int:
        """Exact input size, from the API rather than a heuristic."""
        counted = self._client.messages.count_tokens(
            model=model or self.model, system=system, messages=messages)
        return int(counted.input_tokens)

    def complete(self, *, system: str, messages: list[dict[str, Any]],
                 model: str | None = None, max_tokens: int = 2000,
                 effort: str = "medium") -> Completion:
        """One generation.

        Adaptive thinking, and effort at medium: drafting one short message is
        not the workload that repays the top of the effort range, and the cost
        of that choice is measured on every call rather than assumed.
        """
        chosen = model or self.model
        try:
            response = self._client.beta.messages.create(
                model=chosen, max_tokens=max_tokens, system=system, messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                betas=[FALLBACK_BETA], fallbacks="default")
        except anthropic.APIStatusError as exc:
            raise ModelUnavailable(f"{chosen}: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise ModelUnavailable(f"{chosen}: {exc}") from exc

        refused = response.stop_reason == "refusal"
        text = "".join(block.text for block in response.content
                       if getattr(block, "type", None) == "text")
        return Completion(
            text=text, model=getattr(response, "model", chosen),
            input_tokens=int(response.usage.input_tokens),
            output_tokens=int(response.usage.output_tokens),
            stop_reason=response.stop_reason, refused=refused, raw=response,
            meta={"stop_details": getattr(response, "stop_details", None)})
