"""Bulk, with reasons: one act over many rows, one written reason, and a
refusal that names the rows it refused.

An operator with forty drafts does not click forty times; they open a
spreadsheet instead, and the product loses the day (`docs/28`, OX-7). A
batch is the same act the per-row endpoints perform, over a list, with two
properties the per-row acts do not need.

**One reason, recorded on every row.** Forty approvals with no sentence is
forty clicks, not an act. The reason goes through `zolts.reasons` like every
other override, and it is written into every row's audit entry with the
batch's id, so *which forty, and why* is a `where` clause during the next
incident rather than a memory.

**Never all-or-nothing.** A batch of three where one row is stale — approved
by a colleague a second earlier, completed already, no longer dead — returns
two done and one named refusal. Rolling the two back because of the one
would teach the operator that a batch is fragile, and a fragile batch is one
they stop using. The runtime runs each row under its own savepoint; this
module says what the outcome of that looks like.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from zolts.reasons import refuse_reason

# Enough for a morning's queue, small enough that one request cannot hold a
# transaction open across a thousand rows.
MAX_BATCH = 200

ACTS: Mapping[str, tuple[str, ...]] = {
    "proposals": ("approve", "reject"),
    "tasks": ("complete",),
    "outbox": ("revive", "discard"),
}


class BatchRefusal(str, Enum):
    UNKNOWN_ACT = "unknown_act"
    NO_ROWS = "no_rows"
    TOO_MANY = "too_many"


def dedupe(ids: Iterable[str]) -> list[str]:
    """In order, once each. A row named twice is one row, not two acts."""
    seen: list[str] = []
    for raw in ids:
        value = str(raw).strip()
        if value and value not in seen:
            seen.append(value)
    return seen


def refuse_batch(kind: str, act: str, ids: Iterable[str], reason: str | None) -> str | None:
    """Why the whole batch is refused, or None. A refused batch does nothing;
    a refused row inside an accepted batch is a different thing and is
    reported per row."""
    if act not in ACTS.get(kind, ()):
        return BatchRefusal.UNKNOWN_ACT.value
    rows = dedupe(ids)
    if not rows:
        return BatchRefusal.NO_ROWS.value
    if len(rows) > MAX_BATCH:
        return BatchRefusal.TOO_MANY.value
    refused = refuse_reason(reason)
    return refused.value if refused else None


@dataclass(frozen=True)
class Outcome:
    kind: str
    act: str
    reason: str
    batch_id: str
    done: tuple[str, ...]
    refused: dict[str, str]
    """Row id to the refusal key, in the words the per-row act would use."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "act": self.act, "reason": self.reason,
            "batchId": self.batch_id,
            "done": list(self.done), "refused": dict(self.refused),
            "counts": {"done": len(self.done), "refused": len(self.refused)},
        }


def summarise(kind: str, act: str, reason: str, batch_id: str,
              results: Iterable[tuple[str, str | None]]) -> Outcome:
    """Per-row results — (id, refusal or None) — folded into one outcome."""
    done: list[str] = []
    refused: dict[str, str] = {}
    for row_id, refusal in results:
        if refusal is None:
            done.append(row_id)
        else:
            refused[row_id] = refusal
    return Outcome(kind=kind, act=act, reason=reason.strip(), batch_id=batch_id,
                   done=tuple(done), refused=refused)
