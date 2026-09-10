"""What needs a person, ranked by what it costs to ignore.

The console had nine screens and they were the same screen nine times: a row of
stat tiles, a table, a detail panel, each answering *what is the state of X* for
a different X. None of them answered the only question an operator actually
arrives with — **what do I do now** — so answering it meant opening nine
screens and joining them in your head. A surface that makes you do the joining
is a surface that does not help you work.

The runtime already forms every judgement this needs. A draft is waiting for a
person. A task is past the SLA its step stamped. A mailbox is in alarm. A tier
is missing the p95 two paid plans commit to. The cost per contact is over the
target `docs/08` sets. A programme was published and never activated. Every one
of those was computed, rendered on its own screen, and collected by nothing.

This module is the collection rule, and it is deliberately not a query. It
ranks items the view layer builds **from the same numbers the individual
screens render**, so the home screen and the screen it sends you to cannot
disagree. A summary computed independently of what it summarises is two answers
waiting to diverge, which is how a dashboard starts lying.

**Ranked by the cost of ignoring, never by recency.** A newest-first list is an
inbox, and an inbox is a surface that rewards whoever shouts last. A burning
sending domain outranks a draft waiting for approval because one is reversible
and the other is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Urgency(str, Enum):
    NOW = "now"
    """Something irreversible is happening, or a promise is already broken."""

    TODAY = "today"
    """A clock is running against a commitment somebody made."""

    SOON = "soon"
    """It costs money or momentum if it is still true next week."""


# Every kind of item the surface can raise, in the order it raises them, with
# the sentence that says what ignoring it costs. The order is the product
# decision: it is what an operator's day gets spent on.
#
# The sentence matters as much as the rank. "3 drafts waiting" is a number;
# "nothing in this programme sends until somebody reads them" is a reason to
# click. A worklist without reasons is a to-do list somebody else wrote.
@dataclass(frozen=True)
class Kind:
    key: str
    urgency: Urgency
    view: str
    """Which screen the item's action lives on."""
    cost: str
    """What it costs to leave this alone. Shown to the operator, verbatim."""


ORDER: tuple[Kind, ...] = (
    Kind("sending.alarm", Urgency.NOW, "sending",
         "A domain's reputation is being spent while this is true, and it is "
         "the one thing here that cannot be undone by acting later."),
    Kind("outbox.dead", Urgency.NOW, "programs",
         "An action gave up after its retries. Nothing will deliver it and "
         "nothing else will notice."),
    Kind("task.overdue", Urgency.NOW, "tasks",
         "The SLA this step stamped has already passed. The promise is broken; "
         "the only question left is by how much."),
    Kind("review.waiting", Urgency.TODAY, "review",
         "Nothing in these programmes sends until a person reads them. The "
         "runtime is doing its job and waiting for you to do yours."),
    Kind("spend.ceiling", Urgency.TODAY, "spend",
         "Past the alert. At the ceiling the programmes stop, and finding out "
         "at the ceiling is finding out too late."),
    Kind("sla.missed", Urgency.TODAY, "signals",
         "A tier is missing the p95 the Scale and Enterprise plans commit to "
         "contractually. Every hour it stays true is inside a period somebody "
         "will read back."),
    Kind("task.due", Urgency.SOON, "tasks",
         "Work waiting for a person, still inside its SLA."),
    Kind("cost.over", Urgency.SOON, "spend",
         "Every contact reached is costing more in tokens than the margin "
         "assumes. It compounds with volume rather than with time."),
    Kind("program.draft", Urgency.SOON, "programs",
         "Published and never activated. It enrols nobody and sends nothing "
         "until somebody presses Activate."),
)

BY_KEY: dict[str, Kind] = {k.key: k for k in ORDER}
RANK: dict[str, int] = {k.key: i for i, k in enumerate(ORDER)}


def kind(key: str) -> Kind:
    """The rule for one kind, or a refusal.

    An unknown key is an error rather than a default: a surface that silently
    sorts an item it does not recognise to the bottom is a surface that hides
    the thing nobody thought about.
    """
    if key not in BY_KEY:
        raise KeyError(
            f"{key!r} is not a kind of work this surface knows how to rank; "
            f"add it to ORDER with the cost of ignoring it")
    return BY_KEY[key]


def rank(items: list[dict]) -> list[dict]:
    """Order a worklist by what ignoring each item costs.

    Ties inside a kind keep the order the caller built them in, which is that
    view's own ordering — the review queue's oldest-first, the task queue's
    soonest-deadline-first. A stable sort, so the home screen never disagrees
    with the screen it links to about which item is first.
    """
    return sorted(items, key=lambda item: RANK[kind(item["kind"]).key])


def urgency_counts(items: list[dict]) -> dict[str, int]:
    """How much of each urgency is open, for a surface that shows a number."""
    counts = {level.value: 0 for level in Urgency}
    for item in items:
        counts[kind(item["kind"]).urgency.value] += 1
    return counts
