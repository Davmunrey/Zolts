"""Claim provenance: what a generated message is allowed to assert.

Product invariant, from `docs/08`: every claim in a generated message must map
to a retrieved span, a CRM field, or the approved proof library. A claim
without provenance is removed. If removing it breaks the message, the message
is flagged for a human rather than sent with a hole in it.

This is stricter than "the model usually gets it right", and it is the thing a
CMO asks about before letting a message carrying their brand go out.

Pure logic. The retrieval and the model call happen elsewhere; what happens
here is the decision about what survives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

# A claim is a sentence that asserts something checkable. Splitting on
# sentence boundaries is crude, and deliberately so: a finer decomposition
# would need a model, and a model deciding which of its own claims need
# evidence is the conflict of interest this check exists to remove.
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")

# Sentences that assert nothing checkable. Greetings, questions and calls to
# action carry no factual load, so demanding a citation for them would empty
# every message and teach operators to waive the check.
_NON_ASSERTIVE = re.compile(
    r"^\s*(hi|hello|hey|thanks|thank you|best|regards|kind regards|cheers)\b"
    r"|^\s*(would|could|are|is|do|does|did|can|shall|may|will)\b.*\?\s*$"
    r"|^[^.!?]*\?\s*$",
    re.IGNORECASE,
)

# Numbers, percentages, currency, dates and superlatives are the claims that
# get a company into trouble. A sentence carrying one of these is always
# assertive, whatever else it looks like.
_HARD_CLAIM = re.compile(
    r"\d|%|€|\$|£|\b(most|best|fastest|leading|leader|only|never|always|"
    r"guaranteed|undisputed|#1)\b",
    re.IGNORECASE,
)

# What in a sentence can actually be checked against a source. A number, a
# proper noun, or an absolute — the things that are either in the evidence or
# invented.
_NUMBER = re.compile(r"\d[\d.,]*%?")
_PROPER = re.compile(r"(?<!^)(?<![.!?]\s)\b([A-Z][A-Za-z0-9]{2,})\b")
_ABSOLUTE = re.compile(
    r"\b(most|best|fastest|leading|leader|only|never|always|guaranteed|"
    r"undisputed|unmatched|unparalleled)\b", re.IGNORECASE)


class Source(str, Enum):
    RETRIEVED = "retrieved"   # a span from a document the runtime fetched
    CRM = "crm"               # a field on the tenant's own record
    PROOF = "proof"           # the approved case, figure and logo library


@dataclass(frozen=True)
class Evidence:
    """One thing the runtime can point at."""
    source: Source
    text: str
    ref: str

    @property
    def tokens(self) -> frozenset[str]:
        return _tokens(self.text)


@dataclass(frozen=True)
class Claim:
    text: str
    supported_by: str | None = None
    hard: bool = False

    @property
    def supported(self) -> bool:
        return self.supported_by is not None


@dataclass
class Verdict:
    claims: list[Claim] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    needs_human: bool = False
    reason: str | None = None

    @property
    def message(self) -> str:
        return " ".join(self.kept)

    @property
    def factuality(self) -> float:
        """Share of assertive claims that carry provenance.

        One of the eval scores in `docs/08`. With no assertive claims at all
        the message asserts nothing, which is trivially factual — and caught
        instead by the check that a message with nothing in it is not a
        message.
        """
        if not self.claims:
            return 1.0
        return sum(1 for c in self.claims if c.supported) / len(self.claims)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9€$£%.]+", text.lower()))


def _is_assertive(sentence: str) -> bool:
    if _HARD_CLAIM.search(sentence):
        return True
    return not _NON_ASSERTIVE.search(sentence)


def split_claims(message: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(message or "") if s.strip()]


def anchors(sentence: str) -> frozenset[str]:
    """The checkable parts of a sentence.

    Numbers, proper nouns and absolutes. Everything else — the verbs, the
    connective tissue, the seller's reading of what a signal means — is not a
    claim about the world and has nothing to verify against.

    Measuring coverage over every content word instead was the first version,
    and it dropped the sentence *"Northwind opened four RevOps roles last
    quarter, which usually means the reporting layer is about to be rebuilt"*
    even with that exact fact in evidence: the interpretive clause diluted the
    overlap below the threshold. The check has to bite on the citable half of
    a sentence, or it deletes the writing and keeps nothing.
    """
    found = set(_NUMBER.findall(sentence))
    found |= {m.group(1).lower() for m in _PROPER.finditer(sentence)}
    found |= {m.group(0).lower() for m in _ABSOLUTE.finditer(sentence)}
    return frozenset(a.strip(".,%") for a in found if a.strip(".,%"))


def _match(sentence: str, evidence: list[Evidence], threshold: float) -> str | None:
    """Find evidence that covers this sentence's checkable content.

    Coverage is measured over the sentence's anchors, not the evidence's
    tokens: a long document would otherwise support any sentence by sheer
    surface area, which is how a provenance check becomes decorative.
    """
    checkable = anchors(sentence)
    if not checkable:
        return None
    best_ref, best_score = None, 0.0
    for item in evidence:
        item_anchors = anchors(item.text) | _tokens(item.text)
        covered = len(checkable & item_anchors) / len(checkable)
        if covered > best_score:
            best_ref, best_score = item.ref, covered
    return best_ref if best_score >= threshold else None


_STOPWORDS = frozenset(
    "a an and are as at be been by for from has have i in is it its of on or "
    "our so than that the their them they this to was we were will with you "
    "your".split()
)


def verify(message: str, evidence: list[Evidence], *, threshold: float = 1.0,
           min_kept_claims: int = 1) -> Verdict:
    """Strip every claim that nothing supports.

    `threshold` is the share of a sentence's anchors that one piece of evidence
    must cover. It defaults to 1.0 — every citable thing in the sentence, in
    one source. A sentence that mixes a real figure with an invented one is not
    partly true, and a rule that accepted it at 0.8 would wave through exactly
    the messages this check exists to stop.
    """
    verdict = Verdict()
    for sentence in split_claims(message):
        if not _is_assertive(sentence):
            verdict.kept.append(sentence)
            continue
        if not anchors(sentence):
            # Nothing citable in it. This is the seller's reading of a signal,
            # or a call to action — an opinion is not a claim about the world,
            # and demanding a source for one deletes the writing.
            verdict.kept.append(sentence)
            continue
        ref = _match(sentence, evidence, threshold)
        hard = bool(_HARD_CLAIM.search(sentence))
        verdict.claims.append(Claim(sentence, ref, hard))
        if ref:
            verdict.kept.append(sentence)
        else:
            verdict.dropped.append(sentence)

    if verdict.dropped and not verdict.claims:  # pragma: no cover - defensive
        verdict.needs_human = True

    supported = [c for c in verdict.claims if c.supported]
    if verdict.claims and len(supported) < min_kept_claims:
        # Everything checkable was removed. What is left is a greeting and a
        # call to action, which is not a message — it is the shape of one.
        verdict.needs_human = True
        verdict.reason = "no supported claim survived; the message asserts nothing"
    elif any(c.hard and not c.supported for c in verdict.claims):
        # A dropped number or superlative usually means the model invented a
        # figure. That is worth a human's attention even when the rest stands.
        verdict.needs_human = True
        verdict.reason = "a quantified or superlative claim had no provenance"
    return verdict
