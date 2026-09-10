"""What counts as a reason, when an operator overrides the runtime.

Extracted from `zolts/sendingcontrol` the moment a second act needed it. The
rule is not about sending: it is about a person taking an action the runtime
would not have taken, and the record that act leaves behind.

**A written reason is required, and one that restates the act is refused.**
"Paused", "resume", "fixed", "ok" pass a non-empty check and carry nothing. A
field that accepts them teaches the operator the field is a formality, and a
formality is what the next person types. The row this leaves behind is read
back during an incident by somebody who has only it.

Three refusals rather than one boolean, because "you must give a reason", shown
to somebody who gave one, reads as a broken form.
"""

from __future__ import annotations

from enum import Enum

# Long enough to be a sentence fragment rather than a token. Twelve characters
# is "list bought" plus a letter: short enough not to obstruct somebody acting
# in an incident, long enough that the words below are the only way to be under
# it by accident.
MIN_REASON = 12

# Words that restate the act instead of explaining it. Compared against the
# whole reason once punctuation is stripped, never against a substring: "fixed
# the bounce source in the export" is a reason that happens to contain "fixed".
EMPTY_WORDS: frozenset[str] = frozenset({
    "ok", "okay", "fixed", "done", "resume", "resumed", "unpause", "unpaused",
    "pause", "paused", "stop", "stopped", "start", "started", "test",
    "testing", "n/a", "na", "none", "asap", "now", "please", "retry", "again",
    "requeue", "revive", "discard", "drop", "broken", "error", "failed",
})


class ReasonRefusal(str, Enum):
    NO_REASON = "no_reason"
    """Nothing was written, or only whitespace."""

    REASON_TOO_SHORT = "reason_too_short"
    """Written, but shorter than a sentence fragment."""

    REASON_SAYS_NOTHING = "reason_says_nothing"
    """Written and long enough, and restates the act rather than explaining it."""


def _letters(text: str) -> str:
    """Lowercase, with punctuation flattened to spaces.

    So "fixed!!!" and "fixed" are the same word, and a reason padded to the
    length bound with punctuation is read as what it says rather than as what
    it measures.
    """
    return "".join(c.lower() if c.isalnum() or c == "/" else " " for c in text)


def refuse_reason(text: str | None) -> ReasonRefusal | None:
    """Read a written reason, or say precisely what is wrong with it."""
    if text is None or not text.strip():
        return ReasonRefusal.NO_REASON
    cleaned = text.strip()
    if len(cleaned) < MIN_REASON:
        return ReasonRefusal.REASON_TOO_SHORT
    words = [w for w in _letters(cleaned).split() if w]
    if words and all(word in EMPTY_WORDS for word in words):
        return ReasonRefusal.REASON_SAYS_NOTHING
    return None
