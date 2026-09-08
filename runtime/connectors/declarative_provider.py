"""A data provider described by a document instead of by a connector.

The same decision `docs/18` D-C1 already made for CRMs, for the same reason.
`docs/15` ranks a provider shock — a price change, a policy change, an API
withdrawn — as a high-impact risk and names per-field abstraction with two
providers behind every critical field as the mitigation. A mitigation that
requires an engineer to write a connector before it can be exercised is a
mitigation that arrives a sprint after the shock.

Everything hard here already existed for CRM mappings and is reused rather than
rewritten: the path language with its six transforms, the SSRF guard on the
base URL, and the record extractor. What is new is small — how to phrase the
question, and where in the answer the value is.

    spec:
      transport:
        base_url: https://api.provider.example/v2
        auth: {kind: header, name: x-api-key}
      lookups:
        email:
          path: /find/email
          method: GET
          query:                      # request parameter <- entity path
            full_name: full_name
            domain: account.domain
          response:                   # canonical field <- response path
            email: data.email
          confidence: data.score      # optional
          confidence_max: 100         # the scale that path is expressed in

A lookup whose response mapping produces nothing is a miss, not an error. That
distinction is the whole reason the optimiser can learn: a miss lowers a
measured hit rate, and an error must not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from runtime.connectors.dataprovider import (DataCapabilities, Found, Lookup,
                                             ProviderError)
from runtime.connectors.http import body, request
from zolts.mapping import MISSING, build, check_base_url, extract

# What a provider is allowed to be asked. GET and POST and nothing else: a
# document that can make this runtime issue a DELETE against an arbitrary host
# is a liability rather than a feature.
METHODS = ("GET", "POST")


class ProviderDocumentError(ValueError):
    pass


def validate(document: dict[str, Any]) -> dict[str, Any]:
    """Check a provider document before it can spend money.

    Refuses on the way in rather than on the first lookup, because the first
    lookup happens against a real endpoint with a real credential.
    """
    if document.get("kind") != "DataProvider":
        raise ProviderDocumentError(
            f"expected kind: DataProvider, found {document.get('kind')!r}")
    spec = document.get("spec") or {}
    transport = spec.get("transport") or {}
    base_url = transport.get("base_url")
    if not base_url:
        raise ProviderDocumentError("spec.transport.base_url is required")
    # The same guard the CRM mappings use. A customer-authored document naming
    # 169.254.169.254 would read this runtime's own cloud credentials.
    check_base_url(base_url)

    lookups = spec.get("lookups") or {}
    if not lookups:
        raise ProviderDocumentError("spec.lookups declares no field to resolve")
    for field_name, lookup in lookups.items():
        if not lookup.get("path"):
            raise ProviderDocumentError(f"lookups.{field_name}.path is required")
        method = (lookup.get("method") or "GET").upper()
        if method not in METHODS:
            raise ProviderDocumentError(
                f"lookups.{field_name}.method {method} is not one of {', '.join(METHODS)}")
        if not (lookup.get("response") or {}):
            raise ProviderDocumentError(
                f"lookups.{field_name}.response maps nothing, so every call is a miss")
        _scale_of(field_name, lookup)
    return document


def _scale_of(field_name: str, lookup: dict[str, Any]) -> float:
    """The range the provider's own confidence is expressed in.

    Providers do not agree on it: one returns 0.92 and another returns 92 for
    the same belief. Clamping both into [0, 1] made the second one *certain*
    (D-47), and certainty is what the dossier shows an operator and what the
    waterfall's accuracy floor is read against. So the document states its
    scale, and a scale with no confidence path to scale is refused rather
    than ignored.
    """
    raw = lookup.get("confidence_max")
    if raw is None:
        return 1.0
    try:
        scale = float(raw)
    except (TypeError, ValueError) as exc:
        raise ProviderDocumentError(
            f"lookups.{field_name}.confidence_max is not a number: {raw!r}") from exc
    if scale <= 0:
        raise ProviderDocumentError(
            f"lookups.{field_name}.confidence_max must be above zero, not {scale}")
    if not lookup.get("confidence"):
        raise ProviderDocumentError(
            f"lookups.{field_name}.confidence_max scales a confidence this document "
            f"never reads")
    return scale


def provider_of(document: dict[str, Any]) -> str:
    """The name this document answers to.

    The waterfall looks a provider up by its registration's key and this is
    what the runtime registers it under, so the two have to be the same string
    (D-48). One function, so the CLI and the installer cannot disagree about
    which one wins.
    """
    return str((document.get("metadata") or {}).get("provider") or "declarative")


@dataclass
class DeclarativeDataProvider:
    """One provider document, as a `DataSource`."""
    document: dict[str, Any]
    client: httpx.Client | None = None

    def __post_init__(self) -> None:
        validate(self.document)

    @property
    def _spec(self) -> dict[str, Any]:
        return self.document["spec"]

    @property
    def capabilities(self) -> DataCapabilities:
        return DataCapabilities(
            provider=provider_of(self.document),
            fields=tuple(sorted(self._spec["lookups"])),
            billed_on_miss=bool(self._spec.get("billed_on_miss", False)))

    def _facts(self, lookup: Lookup) -> dict[str, Any]:
        """What the document may read from, flattened into one namespace.

        `account.domain` reaches the account rather than the person, because a
        provider that resolves an email from a name and a company domain needs
        both and should not have to be handed a bespoke payload per caller.
        """
        return {**dict(lookup.entity), "account": dict(lookup.account or {})}

    def _miss(self, **detail: Any) -> Found:
        """A miss, priced.

        A provider that bills for a lookup returning nothing still charged us.
        Reporting zero there understates our own COGS and teaches the
        optimiser that a provider is cheaper than it is — which is exactly the
        provider it would then put first.
        """
        cost = int(self._spec.get("unit_cost_micros", 0))
        return Found(hit=False, cost_micros=cost if self.capabilities.billed_on_miss else 0,
                     detail=detail)

    def resolve(self, lookup: Lookup, credential: str | None,
                config: dict[str, Any]) -> Found:
        spec = self._spec
        declared = (spec.get("lookups") or {}).get(lookup.field)
        if declared is None:
            raise ProviderError(
                f"{self.capabilities.provider} declares no lookup for {lookup.field}")

        facts = self._facts(lookup)
        params = {k: v for k, v in build(facts, declared.get("query") or {}).items() if v}
        payload = {k: v for k, v in build(facts, declared.get("body") or {}).items() if v}
        if not params and not payload:
            # Asking with nothing to go on is a call that cannot hit. Nothing
            # is sent, so nothing is charged even by a bills-on-miss provider.
            return Found(hit=False, detail={"skipped": "no known facts to ask with"})

        transport = spec["transport"]
        auth = transport.get("auth") or {}
        headers, token = {}, None
        if credential:
            if auth.get("kind") == "header" and auth.get("name"):
                headers[auth["name"]] = credential
            elif auth.get("kind") == "query" and auth.get("name"):
                params[auth["name"]] = credential
            else:
                token = credential

        url = f"{transport['base_url'].rstrip('/')}/{declared['path'].lstrip('/')}"
        method = (declared.get("method") or "GET").upper()
        try:
            response = request(method, url, token=token, headers=headers or None,
                               params=params or None, json=payload or None,
                               allow=frozenset({404}), client=self.client)
        except Exception as exc:  # noqa: BLE001 — every transport fault is one thing here
            # An error is the absence of an answer. Raising it rather than
            # returning a miss keeps a broken integration out of the measured
            # hit rate, where it would look like a provider with poor coverage.
            raise ProviderError(f"{self.capabilities.provider}: {exc}") from exc

        # A 404 from a lookup endpoint is the provider saying it has nothing,
        # which is an answer and belongs in the matrix.
        if response.status_code == 404:
            return self._miss(status=404)

        answer = body(response)
        values = {k: v for k, v in build(answer, declared["response"]).items() if v}
        if not values:
            return self._miss(status=response.status_code)

        confidence = 0.0
        if declared.get("confidence"):
            raw = extract(answer, declared["confidence"])
            if raw is not MISSING:
                try:
                    confidence = max(0.0, min(1.0, float(raw) / _scale_of(lookup.field,
                                                                         declared)))
                except (TypeError, ValueError):
                    confidence = 0.0
        return Found(hit=True, values=values,
                     confidence=confidence or float(declared.get("default_confidence", 0.8)),
                     cost_micros=int(spec.get("unit_cost_micros", 0)))
