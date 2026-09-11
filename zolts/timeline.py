"""One timeline per contact: what touched them, what was decided, what went out.

Every row a contact's story needs was already in the database. A signal on the
person or on their account. An enrolment. A policy decision with the rule that
made it and the digest of the pack that held the rule. A proposal with the
evidence behind every sentence and the claims removed for having none. A touch
with its provider and its cost. An outcome. An audit entry. Six tables, each
rendered on its own screen, and no screen that reads one contact across all
six — the register's dominant shape, applied to the one question a customer's
DPO and a customer's CRO both ask in the same words: *why did this person get
this?*

This module holds the rule for what a timeline is. It is deliberately not a
query, for the same reason `zolts.attention` is not: the runtime builds the
events from the rows it already holds, and this decides how they are named,
what each one proves, and how they are ordered. A timeline that names a kind
this module does not know is refused rather than rendered, because a row with
no label is a row the reader skips.

**Newest first, and within one instant, effect before cause.** A decision and
the touch it allowed can share a timestamp to the second. Read top-down, the
touch is the thing that happened and the decision is why, so the touch sits
above it. The rank below is that causal order; the sort reverses it.

**Every kind says what it proves.** A DPO reading this screen is answering a
subject access request (`docs/11`, COMP-2). A CRO is answering *was that email
allowed*. The sentence beside each kind is written for whichever of them is
reading, and it is the same sentence for both.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Kind:
    key: str
    label: str
    """Short, for the row."""
    proves: str
    """What a reader may conclude from a row of this kind. Verbatim on screen."""
    rank: int
    """Causal position within one instant. Higher happened later."""


ORDER: tuple[Kind, ...] = (
    Kind("signal.observed", "Signal",
         "Something the source saw about this person or their account. The "
         "reason any of the rows below exist.", 10),
    Kind("enrollment.entered", "Enrolled",
         "A programme admitted them and assigned an arm. Control arms are "
         "held out and never contacted; that is how lift is measured.", 20),
    Kind("decision.allow", "Allowed",
         "The policy engine permitted a send under a named rule from a "
         "versioned pack. The digest is the rule as it was on the day.", 30),
    Kind("decision.deny", "Refused",
         "The policy engine stopped a send and says which rule. Nothing "
         "reached a provider.", 30),
    Kind("proposal.drafted", "Drafted",
         "An agent wrote this and every sentence maps to the evidence shown, "
         "or was removed. Agents propose; the runtime disposes.", 40),
    Kind("touch.sent", "Sent",
         "Left the building through the named provider at the stated cost.",
         50),
    Kind("touch.queued", "Waiting for a person",
         "A step a human has to do. Nothing was sent.", 50),
    Kind("touch.failed", "Not sent",
         "The runtime tried and the provider or the policy refused. Recorded "
         "so the attempt is not invisible.", 50),
    Kind("outcome.recorded", "Outcome",
         "Something came back: a reply, a meeting, a deal, an opt-out. The "
         "measurement counts it inside the programme's declared window.", 60),
    Kind("enrollment.exited", "Left the programme",
         "Exited under the stated reason. A suppression exit is permanent.",
         70),
    Kind("audit.entry", "Recorded",
         "An operator or the runtime wrote this down, with who and why.", 80),
)

BY_KEY: dict[str, Kind] = {k.key: k for k in ORDER}


def kind(key: str) -> Kind:
    """The rule for one kind, or a refusal.

    Refused rather than defaulted: a row the reader cannot name is a row the
    reader skips, and the row they skip is the one the request was about.
    """
    if key not in BY_KEY:
        raise KeyError(
            f"{key!r} is not a kind of event this timeline knows; add it to "
            f"ORDER with what a reader may conclude from it")
    return BY_KEY[key]


def order(events: list[dict]) -> list[dict]:
    """Newest first; within one instant, effect above cause.

    Stable, so two events of one kind at one instant keep the order the
    runtime produced them in.
    """
    for event in events:
        kind(event["kind"])
    return sorted(events, key=lambda e: (e["at"], BY_KEY[e["kind"]].rank),
                  reverse=True)
