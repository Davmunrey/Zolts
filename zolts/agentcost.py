"""What the model layer costs per contact it reaches, against the target.

`docs/08` sets a **cost target under EUR 0.02 in tokens per contact touched**
and marked it *not measured*: every model call is priced before it is made and
recorded after it, so the number is computable per programme and per period,
and nothing computed it (AGENT-4).

It is the margin number of the agent layer. A product that reaches a contact
for two cents of tokens and one that reaches them for twenty look identical on
every screen the console had, and only one of them has a gross margin.

**In tokens, not in credits.** `cost_event.cost_micros` is what the call cost
this business; `billed_credits` is what the customer is charged for it. The
document targets the first. Measuring the second would report the price list
back to itself and never move.

**Two kinds count.** `agent.generate` and `agent.dossier` are the model calls.
Enrichment, sending and the step tick are priced too and are not tokens; a
denominator of contacts against a numerator that includes a data supplier's
invoice is a different number wearing this one's name.

**The denominator is a person, not an enrolment.** `touch.person_id` names who
a sent touch reached (ADR-056). A touch written before that column existed
names nobody and is left out, which understates the contact count and so
*overstates* the cost per contact — the conservative direction, and the one
that cannot make a missed target look met.
"""

from __future__ import annotations

from enum import Enum

# The target in `docs/08`, as data, in euros per contact touched. A test reads
# the document to check it, and checks it behaviourally: the verdict has to
# flip at this number, because a constant nothing reads is this repository's
# dominant defect.
TARGET_EUR_PER_CONTACT = 0.02

# The priced actions that are model calls. `zolts.billing.CREDITS` prices eight
# kinds and five of them buy something other than tokens.
MODEL_KINDS: frozenset[str] = frozenset({"agent.generate", "agent.dossier"})


class Verdict(str, Enum):
    MEETS = "meets"
    MISSES = "misses"
    NO_DATA = "no_data"
    """No contact has been reached yet. Not the same as costing nothing."""


def judge(eur_per_contact: float | None) -> Verdict:
    """Read a measured cost per contact against the document's target.

    `docs/08` writes *under* EUR 0.02, so exactly two cents misses. The bound
    is strict for the same reason every other one in this repository is: a
    target written strictly and enforced loosely is off by precisely the amount
    somebody will argue about.
    """
    if eur_per_contact is None:
        return Verdict.NO_DATA
    return (Verdict.MEETS if eur_per_contact < TARGET_EUR_PER_CONTACT
            else Verdict.MISSES)
