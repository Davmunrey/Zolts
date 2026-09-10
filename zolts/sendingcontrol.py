"""Stopping a send, and starting one again, as an act with a name on it.

`runtime/breakers.py` is the only thing in this repository that has ever paused
a sending domain. A human could not stop one — not from the console, not from
the command line, not from anywhere. The product's own words for what it does
are *stop a send or pause a burning domain* (`runtime/fleet.py`), and the only
actor able to do either was a cut-off firing after the damage was measurable.

That is the wrong way round for the one resource `docs/09` calls unrecoverable.
An operator who can see a problem before the rates prove it — a campaign
misaddressed, a list bought rather than earned, a partner shouting on the phone
— had to watch and wait for a threshold to agree with them.

The reverse direction existed and was worse than absent. Lifting a pause was a
flag on the command that *registers a domain*, so the only way to say "the
cause is fixed" was to re-declare the domain's SPF, DKIM and DMARC state in the
same breath. Get one flag wrong and the pause lifts while the authentication
record is silently rewritten, which is the failure that arrives as mail
filtered on delivery rather than as an error.

Three rules, and each is here rather than in the endpoint because each is a
product decision that has to hold on every surface that ever offers it.

**A stop and a start both require a written reason.** `zolts/deliverability`
already argues it in one direction — an unexplained pause gets worked around —
and it is at least as true in the other. The audit row that says somebody
lifted a cut-off and not why is the row that gets read back during an incident
by a person who needs exactly the missing half.

**A reason that restates the act is not a reason.** "Paused", "resume", "fixed",
"ok" pass a non-empty check and carry nothing. A field that accepts them
teaches the operator that the field is a formality, and a formality is what
the next person types.

**Resuming while the measurement still says alarm is an override, and is
recorded as one.** The breaker re-trips on the next reputation event, so the
resume is at best temporary. The operator is entitled to do it — they may know
something the rates do not yet — but they are not entitled to have it recorded
as though the domain were healthy. The console says which one they are about to
do *before* the click rather than after.
"""

from __future__ import annotations

from enum import Enum

from zolts.deliverability import ADVISORY, Health, Verdict

# Long enough to be a sentence fragment rather than a token. Twelve characters
# is "list bought" plus a letter: short enough not to obstruct somebody acting
# in an incident, long enough that the words below are the only way to be
# under it by accident.
MIN_REASON = 12

# Words that restate the act instead of explaining it. Compared against the
# whole reason once punctuation is stripped, never against a substring: "fixed
# the bounce source in the export" is a reason that happens to contain "fixed".
EMPTY_WORDS: frozenset[str] = frozenset({
    "ok", "okay", "fixed", "done", "resume", "resumed", "unpause", "unpaused",
    "pause", "paused", "stop", "stopped", "start", "started", "test",
    "testing", "n/a", "na", "none", "asap", "now", "please",
})


class Act(str, Enum):
    PAUSE = "pause"
    RESUME = "resume"


class Refusal(str, Enum):
    """Why an act was not performed. Never a silent no-op."""

    NO_REASON = "no_reason"
    """Nothing was written, or only whitespace."""

    REASON_TOO_SHORT = "reason_too_short"
    """Written, but shorter than a sentence fragment."""

    REASON_SAYS_NOTHING = "reason_says_nothing"
    """Written and long enough, and restates the act rather than explaining it."""

    ALREADY_PAUSED = "already_paused"
    """Asked to stop a domain that is already stopped."""

    ALREADY_SENDING = "already_sending"
    """Asked to lift a pause on a domain that is not paused."""

    NOT_REGISTERED = "not_registered"
    """No such domain. A stop that silently matches nothing is the worst of the
    three outcomes: the operator believes the sending stopped."""


class Resumption(str, Enum):
    """What kind of act a resume turns out to be, given the measurement.

    Three rather than two, because the two ways of resuming an unhealthy domain
    have different consequences and collapsing them would let the worse one
    read as the milder. One lasts until the next reputation event; the other
    lasts.
    """

    CLEAN = "clean"
    """Nothing measured objects. An ordinary lift."""

    OVERRIDE = "override"
    """Over an alarm threshold, under the cut-off. It will not re-trip, and the
    domain is not healthy: capacity is halved while this is true."""

    WILL_RETRIP = "will_retrip"
    """Still over the cut-off the breaker enforces. The lift holds until the
    next reputation event and then the breaker takes it back."""


def refuse_reason(text: str | None) -> Refusal | None:
    """Read a written reason, or say precisely what is wrong with it.

    Three refusals rather than one boolean, because "you must give a reason"
    shown to somebody who gave one is a message that reads as a broken form.
    """
    if text is None or not text.strip():
        return Refusal.NO_REASON
    cleaned = text.strip()
    if len(cleaned) < MIN_REASON:
        return Refusal.REASON_TOO_SHORT
    words = [w for w in _letters(cleaned).split() if w]
    if words and all(word in EMPTY_WORDS for word in words):
        return Refusal.REASON_SAYS_NOTHING
    return None


def _letters(text: str) -> str:
    """Lowercase, with punctuation flattened to spaces.

    So "fixed!!!" and "fixed" are the same word, and a reason padded to the
    length bound with punctuation is read as what it says rather than as what
    it measures.
    """
    return "".join(c.lower() if c.isalnum() or c == "/" else " " for c in text)


def judge_resume(verdict: Verdict) -> Resumption:
    """Is lifting this pause agreeing with the measurement, or overriding it?

    Reads the whole verdict rather than its health, because two of the rules
    that raise an alarm are not objections to sending.

    `assess` reads metrics and never reads the paused column, so its verdict is
    what the rates say *now* rather than what somebody did earlier. Its
    `PAUSED` is therefore the strongest objection available — a rate at or over
    one of the two cut-offs in `docs/09` — and not, as it first reads, a
    restatement of the domain's current state. Mapping it to a clean lift would
    have made the most dangerous resume in the product the one the console
    called ordinary.

    **An advisory rule is not an objection.** `reply.alarm` fires under a 2%
    reply rate, and `zolts/deliverability` says in as many words that cold
    outreach lives just under 2% on a good day — which is why that rule alone
    does not halve a mailbox's capacity. Calling an ordinary reply rate an
    override would make the word mean nothing, which is the same failure as
    calling a live cut-off clean, pointed the other way. `reply.collapsed` is
    deliberately not advisory: engagement that low is a signal the providers
    themselves read.

    A sample too small to rate reads `OK`, and so reads clean. That is the
    honest answer: nothing measured objects, because nothing has been measured.
    Inventing a fourth verdict for it would put a warning in front of an
    operator that no threshold supports.
    """
    if verdict.health is Health.PAUSED:
        return Resumption.WILL_RETRIP
    if verdict.health is Health.ALARM and verdict.rule_key not in ADVISORY:
        return Resumption.OVERRIDE
    return Resumption.CLEAN
