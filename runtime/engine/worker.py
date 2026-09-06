"""The worker loop.

One tick does three things, in this order:

1. Advance enrollments whose next step is due, queueing actions.
2. Claim due actions under a lease and execute them.
3. Leave everything it could not finish in a state another worker can pick up.

Crash safety comes from the lease, not from the process: a worker that dies
holding actions releases them when the lease expires, and the actions carry the
same idempotency keys they had before, so the provider deduplicates the
overlap. At-least-once delivery at the runtime, exactly-once at the provider.
"""

from __future__ import annotations

import logging
import socket
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from runtime.connectors.base import PermanentError, Request, Result, TransientError
from runtime.connectors.registry import get_connector, providers_for
from runtime.crypto import open_sealed
from runtime.db import Database, one
from runtime.engine import gate, generate, planner
from runtime import metering
from runtime.repo import actions, enrollments, entities, ledger, programs, signals

log = logging.getLogger("zolts.worker")


@dataclass
class Tick:
    planned: int = 0
    claimed: int = 0
    succeeded: int = 0
    cancelled: int = 0
    # Held rather than cancelled: the tenant ran out of credits, which no
    # retry fixes and no failure describes.
    deferred: int = 0
    failed: int = 0
    dead: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def did_work(self) -> bool:
        return bool(self.planned or self.claimed)


class Worker:
    def __init__(self, db: Database, *, secret_key: str, lease_seconds: int = 60,
                 batch: int = 25, dry_run: bool = False, name: str | None = None,
                 model_client: Any = None, spend_guard: Any = None) -> None:
        self.db = db
        self.secret_key = secret_key
        # The agent layer is optional: a deployment with no model configured
        # runs every non-agent program unchanged, and an agent step on one
        # fails loudly rather than silently skipping the generation.
        self.model_client = model_client
        self.spend_guard = spend_guard
        self.lease_seconds = lease_seconds
        self.batch = batch
        self.dry_run = dry_run
        self.name = name or f"{socket.gethostname()}/{uuid.uuid4().hex[:8]}"

    # -- scheduling ------------------------------------------------------

    def plan_due(self, tenant_id: str, now: datetime | None = None) -> int:
        """Queue the next step for every enrollment that is due."""
        planned = 0
        with self.db.tenant_tx(tenant_id) as cur:
            for enrollment in enrollments.due(cur, limit=self.batch):
                program = programs.get(cur, str(enrollment["program_id"]))
                if program is None or program["status"] != "live":
                    # A paused program stops producing work but keeps its
                    # enrollments, so resuming it does not lose the sequence.
                    enrollments.advance(cur, str(enrollment["id"]),
                                        step_index=int(enrollment["step_index"]),
                                        state=str(enrollment["state"]), next_run_at=None)
                    continue
                result = planner.plan_next(cur, tenant_id, enrollment, program, now=now)
                if result and result.queued:
                    planned += 1
        return planned

    # -- execution -------------------------------------------------------

    def claim(self) -> list[tuple[str, str]]:
        """Claim due actions across tenants. Returns (action_id, tenant_id)."""
        with self.db.admin_tx() as cur:
            cur.execute("select * from zolts_internal.claim_actions(%s, %s, %s)",
                        (self.name, self.lease_seconds, self.batch))
            return [(str(r["id"]), str(r["tenant_id"])) for r in cur.fetchall()]

    def execute_one(self, action_id: str, tenant_id: str, tick: Tick) -> None:
        with self.db.tenant_tx(tenant_id) as cur:
            action = actions.get(cur, action_id)
            if action is None or action["state"] != "leased":
                return
            try:
                self._dispatch(cur, tenant_id, action, tick)
            except PermanentError as exc:
                actions.cancel(cur, action_id, f"permanent: {exc}")
                tick.cancelled += 1
                tick.errors.append(str(exc))
            except (TransientError, Exception) as exc:  # noqa: BLE001 - recorded, then retried
                state = actions.fail(cur, action_id, f"{type(exc).__name__}: {exc}")
                tick.dead += state == "dead"
                tick.failed += state == "pending"
                tick.errors.append(f"{type(exc).__name__}: {exc}")

    def _dispatch(self, cur, tenant_id: str, action: dict[str, Any], tick: Tick) -> None:
        payload = action["payload"] or {}
        channel = action["channel"] or "task"
        program = programs.get(cur, str(action["program_id"])) if action["program_id"] else None
        spec = program["spec"] if program else {}

        if action["kind"] == "generate":
            self._generate(cur, tenant_id, action, program, spec, tick)
            return

        # A manual step is real work for a human, not a no-op. It is recorded as
        # a queued touch and the action succeeds; the runtime's job was to
        # create the task, not to perform it.
        if action["kind"] == "manual" or payload.get("requires_human"):
            ledger.record_touch(
                cur, tenant_id, enrollment_id=action["enrollment_id"], channel=channel,
                step_key=action["step_key"], idempotency_key=action["idempotency_key"],
                content={"step": payload.get("step", {}), "awaiting": "human_review"},
                provider=None, provider_ref=None, status="queued")
            actions.succeed(cur, str(action["id"]), {"queued_for_human": True})
            tick.succeeded += 1
            return

        # Before anything about this particular action. Credits are the thing
        # being sold, and spending past the ceiling with no decision is how a
        # customer on the smallest plan runs up a bill nobody authorised.
        #
        # First, not last: a tenant who has run out should not also have their
        # work cancelled for a contact that could not be resolved this minute.
        # They have not done anything wrong, so the action is held rather than
        # cancelled and comes back when the period turns.
        cur.execute("select * from tenant where id = %s", (tenant_id,))
        budget = metering.allowance(cur, cur.fetchone(), cost="email.send", units=1)
        if not budget.allowed:
            actions.defer(cur, str(action["id"]), f"budget: {budget.reason}")
            tick.deferred += 1
            return

        person = self._resolve_contact(cur, payload)
        if person is None:
            raise PermanentError("no reachable contact resolved for this action")

        verdict = gate.check(
            cur, tenant_id, person=person, channel=channel,
            enrollment_id=str(action["enrollment_id"]) if action["enrollment_id"] else None,
            program_spec=spec)
        if not verdict.allowed:
            actions.cancel(cur, str(action["id"]),
                           f"{verdict.decision}: {verdict.rule_key}", verdict.decision_id)
            ledger.record_touch(
                cur, tenant_id, enrollment_id=action["enrollment_id"], channel=channel,
                step_key=action["step_key"], idempotency_key=action["idempotency_key"],
                content={"blocked_by": verdict.rule_key, "rationale": verdict.rationale},
                provider=None, provider_ref=None, status="failed")
            tick.cancelled += 1
            return

        provider, secret, config = self._connection_for(cur, channel)
        connector = get_connector(provider)
        result: Result = connector.execute(Request(
            tenant_id=tenant_id, idempotency_key=action["idempotency_key"], channel=channel,
            step=payload.get("step", {}), entity={"id": payload.get("entity_id"),
                                                  "type": payload.get("entity_type")},
            contact=person, secret=secret, config=config, dry_run=self.dry_run))
        if not result.ok:
            raise TransientError(f"{provider} refused the action: {result.detail}")

        ledger.record_touch(
            cur, tenant_id, enrollment_id=action["enrollment_id"], channel=channel,
            step_key=action["step_key"], idempotency_key=action["idempotency_key"],
            content={"step": payload.get("step", {})}, provider=provider,
            provider_ref=result.provider_ref, status="sent",
            cost_micros=result.cost_micros, sent_at=datetime.now(timezone.utc))
        # Metered whatever the provider cost us. Billing a send only when we
        # happen to know our own COGS made every send through a provider that
        # reports no cost free to the customer, which is not a discount
        # anybody decided to give.
        cur.execute("select * from tenant where id = %s", (tenant_id,))
        metering.meter(cur, cur.fetchone(), kind="email.send", units=1,
                       program_id=str(action["program_id"]) if action["program_id"] else None,
                       provider=provider, cost_micros=result.cost_micros or 0)
        actions.succeed(cur, str(action["id"]),
                        {"provider_ref": result.provider_ref, **result.detail},
                        verdict.decision_id)
        tick.succeeded += 1

    def _generate(self, cur, tenant_id: str, action: dict[str, Any],
                  program: dict[str, Any] | None, spec: dict[str, Any], tick: Tick) -> None:
        """Run an agent. Its output is a proposal, never a send."""
        if self.model_client is None or self.spend_guard is None:
            raise PermanentError(
                "this step names an agent but no model client is configured; refusing "
                "to skip the generation and send an empty message")
        if program is None:
            raise PermanentError("the program behind this generation is gone")

        payload = action["payload"] or {}
        person = self._resolve_contact(cur, payload)
        if person is None:
            raise PermanentError("no reachable contact resolved for this generation")
        account = None
        if payload.get("entity_type") == "account":
            account = entities.get_account(cur, payload.get("entity_id"))

        # The policy gate runs before a word is generated. Drafting a message
        # for a contact the runtime may not write to spends money to produce
        # something that can only be thrown away.
        verdict = gate.check(
            cur, tenant_id, person=person, channel=action["channel"] or "email",
            enrollment_id=str(action["enrollment_id"]) if action["enrollment_id"] else None,
            program_spec=spec)

        recent = signals.within_window(
            cur, str(payload.get("entity_id")),
            [e.get("signal") for e in (spec.get("trigger") or {}).get("events", [])
             if e.get("signal")],
            datetime.now(timezone.utc) - timedelta(days=90))

        result = generate.run(
            cur, tenant_id, action, program, client=self.model_client,
            guard=self.spend_guard, signals=recent[:5], person=person, account=account,
            policy_allows=verdict.allowed,
            opt_out=(spec.get("policy") or {}).get("opt_out_text")
                    or "Reply unsubscribe and I will stop.")

        actions.succeed(cur, str(action["id"]),
                        {"proposal_id": result.proposal_id, "state": result.state,
                         "reason": result.reason, "action_id": result.action_id},
                        verdict.decision_id)
        if result.state in {"needs_human", "rejected"}:
            ledger.record_touch(
                cur, tenant_id, enrollment_id=action["enrollment_id"],
                channel=action["channel"] or "email", step_key=action["step_key"],
                idempotency_key=action["idempotency_key"],
                content={"proposal_id": result.proposal_id, "awaiting": result.state,
                         "reason": result.reason},
                provider=None, provider_ref=None, status="queued")
        tick.succeeded += 1

    # -- helpers ---------------------------------------------------------

    def _resolve_contact(self, cur, payload: dict[str, Any]) -> dict[str, Any] | None:
        entity_type = payload.get("entity_type")
        entity_id = payload.get("entity_id")
        if entity_type == "person":
            return entities.get_person(cur, entity_id)
        roles = (payload.get("step") or {}).get("buying_roles")
        contacts = entities.contacts_for_account(cur, entity_id, roles=roles, limit=1)
        return contacts[0] if contacts else None

    def _connection_for(self, cur, channel: str) -> tuple[str, str | None, dict[str, Any]]:
        """Pick the tenant's active connector for a channel.

        A tenant with no connection for the channel falls back to the fake
        connector only in dry run. Otherwise it is a permanent error: silently
        doing nothing while reporting success is the failure mode that makes a
        sending platform untrustworthy.
        """
        candidates = providers_for(channel)
        if candidates:
            cur.execute(
                "select * from connection where provider = any(%s) and status = 'active'"
                " order by updated_at desc limit 1", (candidates,))
            row = one(cur)
            if row is not None:
                return (row["provider"], open_sealed(row["secret_enc"], self.secret_key),
                        row["config"] or {})
        if self.dry_run and "fake" in candidates:
            return ("fake", None, {})
        raise PermanentError(f"tenant has no active connection for channel '{channel}'")

    # -- loop ------------------------------------------------------------

    def tick(self, tenant_ids: list[str] | None = None) -> Tick:
        t = Tick()
        for tenant_id in tenant_ids or self.active_tenants():
            try:
                t.planned += self.plan_due(tenant_id)
            except Exception as exc:  # noqa: BLE001 - one tenant must not stop the rest
                t.errors.append(f"plan[{tenant_id}]: {exc}")
        claimed = self.claim()
        t.claimed = len(claimed)
        for action_id, tenant_id in claimed:
            self.execute_one(action_id, tenant_id, t)
        return t

    def active_tenants(self) -> list[str]:
        with self.db.admin_tx() as cur:
            cur.execute("select id from tenant where status = 'active'")
            return [str(r["id"]) for r in cur.fetchall()]

    def run(self, *, interval: float = 2.0, max_ticks: int | None = None) -> None:
        ticks = 0
        while max_ticks is None or ticks < max_ticks:
            t = self.tick()
            if t.errors:
                for err in t.errors[:5]:
                    log.warning("worker error: %s", err)
            if not t.did_work:
                time.sleep(interval)
            ticks += 1
