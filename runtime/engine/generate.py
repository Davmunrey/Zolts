"""Running an agent, and deciding what its output is allowed to become.

This is where ADR-004 is enforced rather than asserted. The agent returns a
draft; this module writes it as a proposal and then decides — from the eval
gate, the policy gate and the tenant's own setting — whether it becomes a
queued send or a row in someone's review queue.

Nothing here calls a provider. The dispatch it may enqueue goes through the
same outbox, the same idempotency key and the same policy gate as a step that
no agent touched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from runtime.agents import copywriter
from runtime.agents.client import ModelClient
from runtime.agents.spend import SpendGuard
from runtime.repo import actions, entities, ledger, proposals
from zolts.provenance import Evidence, Source

log = logging.getLogger("zolts.generate")

# A generated send is a distinct step from the generation that produced it, so
# it gets its own key. Sharing one would make the proposal and the send
# collide in the outbox.
SEND_SUFFIX = ":send"


@dataclass
class Generated:
    proposal_id: str | None
    state: str
    reason: str
    action_id: str | None = None
    cost_micros: int = 0


def dossier(cur, person: dict[str, Any], account: dict[str, Any] | None,
            signals: list[dict[str, Any]]) -> list[Evidence]:
    """Assemble what the agent is allowed to say, by retrieval.

    Selective, not a dump of the record: dumping is expensive, noisy and an
    unnecessary PII exposure, and every field that reaches the prompt is a
    field that can end up in the message.
    """
    # Every item names its own subject. A citation that reads "raised 9,000,000"
    # supports no sentence on its own, because the verifier asks one source to
    # cover a whole sentence — and a sentence names who did the thing. Splitting
    # the company out into its own item made every natural sentence
    # unsupportable, which is a retrieval bug that looks like a model failure.
    evidence: list[Evidence] = []
    company = (account or {}).get("name")
    if company:
        evidence.append(Evidence(Source.CRM, f"{company} is the account.", "crm.account"))
        band = account.get("employee_band")
        if band:
            evidence.append(Evidence(Source.CRM, f"{company} has {band} employees.",
                                     "crm.employee_band"))
        country = account.get("country")
        if country:
            evidence.append(Evidence(Source.CRM, f"{company} is based in {country}.",
                                     "crm.country"))
    full_name = person.get("full_name")
    if full_name:
        subject = f"{full_name} works at {company}" if company else f"{full_name} is the contact"
        evidence.append(Evidence(Source.CRM, f"{subject}.", "crm.contact"))
    for signal in signals:
        payload = signal.get("payload") or {}
        rendered = ", ".join(f"{k} {v}" for k, v in payload.items())
        subject = f"{company}: " if company else ""
        evidence.append(Evidence(Source.RETRIEVED,
                                 f"{subject}{signal['type']} with {rendered}.",
                                 f"signal.{str(signal['id'])[:8]}"))
    return evidence


def _budget(spec: dict[str, Any]) -> float | None:
    """The program's declared ceiling, in dollars.

    Credits are the billing unit; the guard speaks dollars. One credit is one
    cent — stated here rather than buried, because a wrong conversion would
    make every budget a hundred times too generous.
    """
    budget = spec.get("budget") or {}
    credits = budget.get("monthly_credits")
    return float(credits) / 100.0 if credits else None


def run(cur, tenant_id: str, action: dict[str, Any], program: dict[str, Any], *,
        client: ModelClient, guard: SpendGuard, signals: list[dict[str, Any]],
        person: dict[str, Any], account: dict[str, Any] | None,
        policy_allows: bool, opt_out: str,
        requires_ai_disclosure: bool = False) -> Generated:
    payload = action["payload"] or {}
    spec = program["spec"]
    tier = payload.get("tier") or "t2"
    play = (spec.get("plays") or {}).get(tier, {})
    requires = play.get("auto_send_requires") or {}
    key = action["idempotency_key"]

    evidence = dossier(cur, person, account, signals)
    if not evidence:
        # A message with no evidence cannot carry a claim, so it cannot be
        # written. This is a data problem, not a model problem, and saying so
        # is more useful than an empty draft.
        proposal = proposals.record(
            cur, tenant_id, agent="copywriter", idempotency_key=key, content={},
            evidence=[], evaluation={}, eval_score=None, spend={}, cost_micros=0,
            state="rejected", gate_reason="no evidence retrieved for this contact",
            enrollment_id=action["enrollment_id"], program_id=action["program_id"],
            step_key=action["step_key"], channel=action["channel"])
        return Generated(str(proposal["id"]) if proposal else None, "rejected",
                         "no evidence retrieved for this contact")

    spent_usd = ledger.spend_micros(cur, str(action["program_id"])
                                    if action["program_id"] else None) / 1_000_000

    result = copywriter.draft(
        client=client, guard=guard, evidence=evidence, tier=tier, opt_out=opt_out,
        policy_allows=policy_allows,
        tenant_enabled=bool(play.get("auto_send")),
        consumed_usd=spent_usd, limit_usd=_budget(spec),
        threshold=requires.get("eval_score"),
        requires_ai_disclosure=requires_ai_disclosure)

    state = result.state
    proposal = proposals.record(
        cur, tenant_id, agent="copywriter", idempotency_key=key,
        content=result.as_content(),
        evidence=[{"source": e.source.value, "ref": e.ref, "text": e.text}
                  for e in result.evidence],
        evaluation={"score": result.gate.score, "threshold": result.gate.threshold,
                    "checks": [{"name": c.name, "outcome": c.outcome.value,
                                "score": c.score, "detail": c.detail}
                               for c in result.evaluation.checks + result.evaluation.compliance],
                    "factuality": result.verification.factuality,
                    "dropped": result.verification.dropped},
        eval_score=result.gate.score, spend=result.spend.raw,
        cost_micros=result.cost_micros, state=state,
        gate_reason=result.blocked or result.gate.reason, model=result.model,
        prompt_version=result.prompt_version,
        enrollment_id=action["enrollment_id"], program_id=action["program_id"],
        step_key=action["step_key"], channel=action["channel"])
    if proposal is None:
        return Generated(None, "superseded", "a proposal for this step already exists")

    if result.cost_micros:
        ledger.record_cost(cur, tenant_id,
                           program_id=str(action["program_id"]) if action["program_id"] else None,
                           kind="llm", provider=result.model, units=1,
                           cost_micros=result.cost_micros)

    ledger.audit(cur, tenant_id, actor="agent:copywriter", action="proposal.created",
                 subject=str(proposal["id"]),
                 detail={"state": state, "model": result.model,
                         "score": result.gate.score, "reason": result.gate.reason})

    action_id = None
    if state == "approved":
        action_id = promote(cur, tenant_id, proposal, action)
    return Generated(str(proposal["id"]), state,
                     result.blocked or result.gate.reason, action_id, result.cost_micros)


def promote(cur, tenant_id: str, proposal: dict[str, Any],
            source_action: dict[str, Any] | None = None,
            approved_by: str = "gate") -> str | None:
    """Turn an approved proposal into a queued send.

    The only path from generated text to a provider. It runs in the caller's
    transaction alongside the state change, so a proposal cannot be marked
    dispatched without the action that dispatches it existing.
    """
    body = (proposal["content"] or {}).get("body")
    if not body:
        return None
    key = f"{proposal['idempotency_key']}{SEND_SUFFIX}"
    row = actions.enqueue(
        cur, tenant_id, kind="dispatch", idempotency_key=key,
        payload={"step": {"step": proposal["step_key"], "channel": proposal["channel"],
                          "body": body},
                 "entity_type": (source_action or {}).get("payload", {}).get("entity_type"),
                 "entity_id": (source_action or {}).get("payload", {}).get("entity_id"),
                 "proposal_id": str(proposal["id"]), "requires_human": False},
        enrollment_id=proposal["enrollment_id"], program_id=proposal["program_id"],
        channel=proposal["channel"], step_key=proposal["step_key"])
    action_id = str(row["id"]) if row else None
    proposals.decide(cur, str(proposal["id"]), state="dispatched",
                     approved_by=approved_by, action_id=action_id)
    ledger.audit(cur, tenant_id, actor=approved_by, action="proposal.dispatched",
                 subject=str(proposal["id"]), detail={"action_id": action_id})
    return action_id
