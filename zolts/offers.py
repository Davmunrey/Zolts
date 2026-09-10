"""What commercial latitude a generated message may offer.

A blueprint declares `policy.discount_authority` — `ecommerce-dtc` says
`{max_pct: 15}` — and nothing read it, so nothing constrained the copywriter
against it (D-85). Provenance (ADR-012) asks whether a claim is *true*: every
statement must map to a retrieved span, a CRM field or the proof library. This
asks a different question — whether we are *allowed to say it* — and no amount
of evidence makes an unauthorised offer permissible. A message can cite its
source perfectly and still commit the seller to terms the archetype forbids.

That difference is why an offer over the limit is never dropped the way an
unsupported claim is. `zolts/provenance.py` states the rule: if removing a
claim breaks the message, the message goes to a person rather than out with a
hole. Deleting the offer and sending the rest is exactly that case.

Pure logic, like provenance: the sentence splitting is shared with it, so
"what counts as a sentence" has one answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from zolts.provenance import split_claims

# The words that turn a number into an offer. Without one of these a figure is
# a fact about the world — "20% of our customers", "we grew 30% last year" —
# and demanding review for those would empty the check's credibility.
_CUE = re.compile(
    r"\b(off|discount|discounted|discounts|save|saves|saving|savings|reduction|"
    r"rebate|waive|waived|waiver|refund|cashback|credit back|money back|"
    r"free month|months free|first month free)\b",
    re.IGNORECASE,
)

_PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:%|per ?cent\b)", re.IGNORECASE)

# A currency amount, symbol-first or unit-last. Kept narrow on purpose: a bare
# number beside a cue ("save 3 hours a week") is not money and must not be read
# as an offer.
_AMOUNT = re.compile(
    r"(?:([€$£])\s*(\d[\d.,]*))"
    r"|(?:(\d[\d.,]*)\s*(EUR|USD|GBP|euros?|dollars?|pounds?)\b)",
    re.IGNORECASE,
)

PERCENT, AMOUNT = "percent", "amount"


@dataclass(frozen=True)
class Offer:
    """One piece of commercial latitude a message extends."""
    sentence: str
    kind: str
    value: float
    unit: str | None = None


@dataclass(frozen=True)
class Authority:
    """What the archetype permits. `None` everywhere means unlimited.

    A blueprint that declares no `discount_authority` has said nothing, and
    silence is not a ceiling of zero: reading it as one would send every
    programme without an authority to a human on its first discount.
    """
    max_pct: float | None = None

    @classmethod
    def from_policy(cls, policy: dict | None) -> "Authority | None":
        block = (policy or {}).get("discount_authority")
        if not isinstance(block, dict):
            return None
        raw = block.get("max_pct")
        return cls(max_pct=None if raw is None else float(raw))

    @property
    def declares_anything(self) -> bool:
        return self.max_pct is not None


def _number(text: str) -> float:
    """`1.234,56` and `1,234.56` both mean the same amount to a reader.

    The last separator is the decimal one when it leaves two digits behind it;
    otherwise every separator is grouping. A discount of `1,5` is one and a
    half per cent in half of Europe and fifteen in the other half, and reading
    it as fifteen is the direction that sends the offer out.
    """
    cleaned = text.strip()
    tail = re.search(r"[.,](\d{1,2})$", cleaned)
    if tail:
        head = cleaned[:tail.start()].replace(",", "").replace(".", "")
        return float(f"{head or '0'}.{tail.group(1)}")
    # Every separator is grouping: `1,234` is a thousand and not one and a bit,
    # because a group is three digits and a decimal part is one or two.
    return float(cleaned.replace(",", "").replace(".", ""))


def find(message: str) -> list[Offer]:
    """Every offer the message makes, in the order it makes them.

    A sentence, not a claim: `provenance.split_claims` returns every sentence,
    and an offer inside a question — *would you like 20% off?* — is an offer.
    """
    out: list[Offer] = []
    for sentence in split_claims(message or ""):
        if not _CUE.search(sentence):
            continue
        for match in _PERCENT.finditer(sentence):
            out.append(Offer(sentence=sentence.strip(), kind=PERCENT,
                             value=_number(match.group(1))))
        for match in _AMOUNT.finditer(sentence):
            symbol, after_symbol, before_unit, unit = match.groups()
            out.append(Offer(sentence=sentence.strip(), kind=AMOUNT,
                             value=_number(after_symbol or before_unit),
                             unit=(symbol or unit or "").strip()))
    return out


def exceeding(message: str, authority: Authority | None) -> list[Offer]:
    """The offers a person has to see before this message may send.

    Three cases, and the middle one is the decision:

    * A percentage above the ceiling. The archetype forbids it.
    * **A currency amount, against a percentage ceiling.** `€500 off` cannot be
      compared with `max_pct: 15` without an order value nobody supplied, so
      the honest answer is that the declared authority cannot judge it — and
      passing what cannot be judged is the flattering direction. Adding
      `max_amount` to the authority makes it comparable, and is registered as
      the alternative rather than assumed here (decision 52).
    * Anything else passes. A percentage at or below the ceiling is permitted,
      and a message with no offer in it has nothing to review.
    """
    if authority is None or not authority.declares_anything:
        return []
    out: list[Offer] = []
    for offer in find(message):
        if offer.kind == PERCENT and authority.max_pct is not None:
            if offer.value > authority.max_pct:
                out.append(offer)
        elif offer.kind == AMOUNT:
            out.append(offer)
    return out


def reason(offers: list[Offer], authority: Authority) -> str:
    """Why this message needs a person, in words an operator can act on."""
    first = offers[0]
    if first.kind == PERCENT:
        return (f"offers {first.value:g}% against an authority of "
                f"{authority.max_pct:g}%")
    return (f"offers {first.unit}{first.value:g}, which a percentage authority "
            f"of {authority.max_pct:g}% cannot judge")
