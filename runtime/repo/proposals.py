"""Agent proposals: the record of what was drafted and who let it through."""

from __future__ import annotations

import json
from typing import Any

from runtime.db import one, rows


def record(cur, tenant_id: str, *, agent: str, idempotency_key: str,
           content: dict[str, Any], evidence: list[dict[str, Any]],
           evaluation: dict[str, Any], eval_score: float | None,
           spend: dict[str, Any], cost_micros: int, state: str,
           gate_reason: str | None, model: str | None = None,
           prompt_version: str | None = None, enrollment_id: str | None = None,
           program_id: str | None = None, step_key: str | None = None,
           channel: str | None = None) -> dict[str, Any] | None:
    """Write a proposal. Returns None when this step already has one."""
    cur.execute(
        "insert into proposal (tenant_id, enrollment_id, program_id, agent, step_key,"
        " channel, idempotency_key, model, prompt_version, content, evidence, eval,"
        " eval_score, spend, cost_micros, state, gate_reason)"
        " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " on conflict (tenant_id, idempotency_key) do nothing returning *",
        (tenant_id, enrollment_id, program_id, agent, step_key, channel, idempotency_key,
         model, prompt_version, json.dumps(content), json.dumps(evidence),
         json.dumps(evaluation), eval_score, json.dumps(spend), cost_micros, state,
         gate_reason))
    return one(cur)


def get(cur, proposal_id: str) -> dict[str, Any] | None:
    cur.execute("select * from proposal where id = %s", (proposal_id,))
    return one(cur)


def queue(cur, limit: int = 100) -> list[dict[str, Any]]:
    """What is waiting for a person. The review queue the console counts."""
    cur.execute(
        "select * from proposal where state in ('draft','needs_human')"
        " order by created_at limit %s", (limit,))
    return rows(cur)


# States a proposal can still be moved out of. `approved` is here because the
# gate writes that state directly and the promotion then has to move it to
# `dispatched`; leaving it out made an auto-approved proposal impossible to
# dispatch, which is the whole path the gate exists to open.
DECIDABLE = ("draft", "needs_human", "approved")


def decide(cur, proposal_id: str, *, state: str, approved_by: str,
           action_id: str | None = None) -> dict[str, Any] | None:
    """Approve, reject or dispatch. Terminal states cannot be re-decided.

    The guard is in the WHERE clause rather than in the caller: two reviewers
    opening the same item is ordinary, and the second one must not be able to
    re-approve something already dispatched.
    """
    cur.execute(
        "update proposal set state = %s, approved_by = %s, action_id = %s,"
        " decided_at = now() where id = %s and state = any(%s) returning *",
        (state, approved_by, action_id, proposal_id, list(DECIDABLE)))
    return one(cur)


def counts(cur) -> dict[str, int]:
    cur.execute("select state, count(*) as n from proposal group by state")
    return {r["state"]: int(r["n"]) for r in cur.fetchall()}
