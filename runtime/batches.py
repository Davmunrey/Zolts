"""Running a batch: the same per-row acts, each under its own savepoint, with
the batch's reason and id written into every row's audit entry.

`zolts.batches` says what a batch is and what its outcome looks like. This
performs it. Every row is the act the per-row endpoint performs — promote a
proposal, reject it, complete a task, revive or discard a dead action — and
a row that cannot be acted on is a named refusal in the words that endpoint
would have used, not an exception that takes the other rows with it.

**A savepoint per row.** A failing statement aborts the whole transaction
and everything after it; without the savepoint a stale row in position two
would roll back position one and stop position three. With it, the batch
is what its name says: many rows, each judged on its own.
"""

from __future__ import annotations

import uuid
from typing import Any

from runtime import outbox
from runtime.engine import generate
from runtime.repo import ledger, proposals, tasks as tasks_repo
from zolts.batches import MAX_BATCH, Outcome, dedupe, refuse_batch, summarise

__all__ = ["MAX_BATCH", "BatchRefused", "run"]


class BatchRefused(ValueError):
    """The whole batch, before any row: the key names why."""

    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


def _proposal(cur, tenant_id: str, act: str, proposal_id: str, *, reason: str,
              actor: str, batch_id: str) -> str | None:
    proposal = proposals.get(cur, proposal_id)
    if proposal is None:
        return "not_found"
    if proposal["state"] not in proposals.DECIDABLE:
        return f"already_{proposal['state']}"
    if act == "approve":
        action_id = generate.promote(cur, tenant_id, proposal, approved_by=actor)
        if action_id is None:
            return "no_body"
        ledger.audit(cur, tenant_id, actor=actor, action="proposal.approved",
                     subject=proposal_id,
                     detail={"reason": reason, "batch": batch_id, "action_id": action_id})
        return None
    row = proposals.decide(cur, proposal_id, state="rejected", approved_by=actor)
    if row is None:
        return "already_decided"
    ledger.audit(cur, tenant_id, actor=actor, action="proposal.rejected",
                 subject=proposal_id, detail={"reason": reason, "batch": batch_id})
    return None


def _task(cur, tenant_id: str, task_id: str, *, reason: str, actor: str,
          batch_id: str) -> str | None:
    row = tasks_repo.complete(cur, task_id, by=actor)
    if row is None:
        return "not_a_task_or_already_done"
    ledger.audit(cur, tenant_id, actor=actor, action="task.completed", subject=task_id,
                 detail={"step": row["step_key"], "reason": reason, "batch": batch_id,
                         "late": bool(row["due_at"] and row["completed_at"] > row["due_at"])})
    return None


def _dead(cur, tenant_id: str, act: str, action_id: str, *, reason: str, actor: str,
          batch_id: str) -> str | None:
    run = outbox.revive if act == "revive" else outbox.discard
    outcome = run(cur, tenant_id, action_id, reason=reason, actor=actor)
    if not outcome.done:
        return outcome.refusal.value
    # The per-row act wrote its own audit entry with the reason; the batch id
    # is added beside it so the forty are one query.
    ledger.audit(cur, tenant_id, actor=actor, action=f"action.{act}.batch",
                 subject=action_id, detail={"reason": reason, "batch": batch_id})
    return None


def run(cur, tenant_id: str, *, kind: str, act: str, ids: list[str], reason: str | None,
        actor: str) -> Outcome:
    refused = refuse_batch(kind, act, ids, reason)
    if refused:
        raise BatchRefused(refused)
    batch_id = uuid.uuid4().hex
    text = str(reason).strip()
    results: list[tuple[str, str | None]] = []
    for row_id in dedupe(ids):
        try:
            with cur.connection.transaction():
                if kind == "proposals":
                    verdict = _proposal(cur, tenant_id, act, row_id, reason=text,
                                        actor=actor, batch_id=batch_id)
                elif kind == "tasks":
                    verdict = _task(cur, tenant_id, row_id, reason=text, actor=actor,
                                    batch_id=batch_id)
                else:
                    verdict = _dead(cur, tenant_id, act, row_id, reason=text, actor=actor,
                                    batch_id=batch_id)
        except Exception as exc:  # the row's savepoint rolled back; the rest proceed
            verdict = f"failed: {type(exc).__name__}"
        results.append((row_id, verdict))
    outcome = summarise(kind, act, text, batch_id, results)
    ledger.audit(cur, tenant_id, actor=actor, action=f"{kind}.batch",
                 subject=batch_id,
                 detail={"act": act, "reason": text, "done": list(outcome.done),
                         "refused": dict(outcome.refused)})
    return outcome
