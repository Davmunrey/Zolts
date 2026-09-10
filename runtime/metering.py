"""Counting what a tenant owes, as they spend it.

The price list lives in `zolts.billing`; which period a tenant is in and how
much they have consumed live here. The split is the usual one: what something
costs is arithmetic, when and to whom is a database.

Two rules, and the second is the one that matters.

**Every metered action is priced at the moment it happens.** A cost event that
records euros and not credits is a row nobody can invoice — that was the state
of every row this runtime had ever written.

**A tenant over their plan is stopped, not silently extended.** Credits are the
thing being sold; letting a runtime spend past the ceiling with no decision is
how a €490 customer runs up a €4,000 bill on a mistake in their own program.
The stop is checked before the spend, and it fails closed like every other
gate here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from runtime.db import Database, one
from zolts.billing import (NEGOTIATED, BillingError, Plan, check_tiers, credits_for,
                           plan_for, statement)


# `docs/18` I6: "overage with an 80% alert and a configurable hard ceiling".
# The alert fires before the ceiling does, because the point of it is that
# nobody is surprised — a customer who finds out at the ceiling found out too
# late to do anything but stop.
ALERT_AT = Decimal("0.80")


@dataclass(frozen=True)
class Allowance:
    """Whether this tenant may spend, and what is left."""
    allowed: bool
    remaining: Decimal
    consumed: Decimal
    included: Decimal
    ceiling: Decimal
    reason: str | None = None

    @property
    def share_used(self) -> Decimal:
        """Of the ceiling, not of the plan: it is the ceiling that stops work."""
        if self.ceiling <= 0:
            return Decimal("1")
        return (self.consumed / self.ceiling).quantize(Decimal("0.0001"))

    @property
    def alerting(self) -> bool:
        return self.share_used >= ALERT_AT

    @property
    def overage(self) -> Decimal:
        """Credits consumed beyond the plan. Zero until the ceiling is raised."""
        return max(Decimal("0"), self.consumed - self.included)


def _tiers(raw: Any) -> tuple[tuple[Decimal | None, Decimal], ...] | None:
    """A negotiated credit ladder, as stored in `tenant.negotiated_terms`.

    Shape: `[[100000, 0.009], [null, 0.007]]` — an upper bound and a rate per
    band, the last one open-ended. Validated on the way in rather than on the
    way to an invoice: a ladder with a gap in it prices some volume at nothing.
    """
    if raw is None:
        return None
    try:
        ladder = tuple((None if upper is None else Decimal(str(upper)), Decimal(str(rate)))
                       for upper, rate in raw)
    except (TypeError, ValueError) as exc:
        raise BillingError(
            f"negotiated credit_tiers must be [[upper|null, rate], ...]: {exc}") from exc
    check_tiers(ladder)
    return ladder


def _terms(tenant: dict[str, Any]) -> Plan:
    plan_key = tenant["plan"]
    if plan_key == NEGOTIATED:
        negotiated = tenant.get("negotiated_terms") or {}
        missing = {"platform_eur", "seats", "credits"} - set(negotiated)
        if missing:
            raise BillingError(
                f"enterprise tenant is missing negotiated terms: "
                f"{', '.join(sorted(missing))}")
        return Plan(key=NEGOTIATED, name="Enterprise",
                    platform_eur=Decimal(str(negotiated["platform_eur"])),
                    seats=int(negotiated["seats"]),
                    credits=Decimal(str(negotiated["credits"])),
                    credit_tiers=_tiers(negotiated.get("credit_tiers")),
                    seat_eur=(None if negotiated.get("seat_eur") is None
                              else Decimal(str(negotiated["seat_eur"]))))
    plan = plan_for(plan_key)
    assert plan is not None  # plan_for only returns None for NEGOTIATED
    return plan


def _month_bounds(moment: datetime) -> tuple[datetime, datetime]:
    start = moment.astimezone(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0)
    # The first of the next month, found by stepping past the longest one.
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def open_period(cur, tenant: dict[str, Any],
                moment: datetime | None = None) -> dict[str, Any]:
    """The tenant's open period, opened if there is none.

    The plan's terms are copied in rather than read at close time. A customer
    who upgrades mid-month is billed on what they were sold for the period they
    consumed, and a price list that changes must not restate a closed month.
    """
    cur.execute("select * from billing_period where tenant_id = %s"
                " and closed_at is null", (str(tenant["id"]),))
    existing = one(cur)
    if existing is not None:
        return existing

    plan = _terms(tenant)
    starts_at, ends_at = _month_bounds(moment or datetime.now(timezone.utc))
    cur.execute(
        "insert into billing_period (tenant_id, starts_at, ends_at, plan,"
        " included_credits, platform_eur, seats_included)"
        " values (%s,%s,%s,%s,%s,%s,%s) returning *",
        (str(tenant["id"]), starts_at, ends_at, plan.key, plan.credits,
         plan.platform_eur, plan.seats))
    return one(cur)


def allowance(cur, tenant: dict[str, Any], *, cost: str | None = None,
              units: float = 1) -> Allowance:
    """Whether a tenant may spend, before they do.

    `cost` names the action about to happen, so the answer accounts for it: a
    tenant with two credits left may send two emails and not a dossier.

    The stop is the ceiling, not the plan. They are the same number until
    somebody raises the ceiling, which is the deliberate act that overage bills
    for — a tenant still cannot overspend by accident.
    """
    period = open_period(cur, tenant)
    consumed = Decimal(str(period["consumed_credits"]))
    included = Decimal(str(period["included_credits"]))
    raised = tenant.get("credit_ceiling")
    ceiling = included if raised is None else Decimal(str(raised))
    wanted = credits_for(cost, units) if cost else Decimal("0")
    left = ceiling - consumed

    if left - wanted < 0:
        limit = (f"this period's {included} credits are spent" if ceiling == included
                 else f"the {ceiling}-credit ceiling is reached")
        return Allowance(
            allowed=False, remaining=left, consumed=consumed, included=included,
            ceiling=ceiling,
            reason=(f"{limit} ({consumed} used); "
                    f"{cost or 'the action'} needs {wanted}"))
    return Allowance(allowed=True, remaining=left, consumed=consumed,
                     included=included, ceiling=ceiling)


def meter(cur, tenant: dict[str, Any], *, kind: str, units: float = 1,
          program_id: str | None = None, provider: str | None = None,
          cost_micros: int = 0) -> Decimal:
    """Record what an action cost, in credits and in money, against the period.

    The credit price is not optional and an unknown kind raises: a silent zero
    is how a category of usage becomes free without anybody deciding it should
    be.
    """
    from runtime.repo import ledger

    period = open_period(cur, tenant)
    billed = credits_for(kind, units)
    ledger.record_cost(cur, str(tenant["id"]), program_id=program_id, kind=kind,
                       provider=provider, units=units, cost_micros=cost_micros,
                       billed_credits=float(billed),
                       billing_period_id=str(period["id"]))
    cur.execute(
        "update billing_period set consumed_credits = consumed_credits + %s"
        " where id = %s", (billed, period["id"]))
    return billed


# What one step of a program costs, per `docs/12`.
STEP_KIND = "program.step"


def meter_step(cur, tenant: dict[str, Any], action_id: str) -> Decimal:
    """Bill one program step, once, and only one the runtime actually ran.

    `docs/12` prices this as **program execution**, not as program queueing,
    and the two are different events. The planner queues one step at a time
    and an exit rule cancels what is still pending, so a step billed at the
    queue is a step a reply or an opt-out correctly threw away — charged for.

    The billing mark goes on the action's own row because the action *is* the
    step: an outbox row already carrying the idempotency key that makes the
    step unique within its enrollment. A retried action therefore bills once
    by construction rather than by a caller remembering to check.

    The `succeeded` condition is the rule, not a guard: a step the policy gate
    refused, or one deferred for budget or capacity, leaves no charge. Billing
    a step our own compliance engine blocked would make the safest
    configuration the most expensive one to run, which is the incentive
    `docs/11` exists to prevent.
    """
    cur.execute(
        "update action set billed_at = now()"
        " where id = %s and billed_at is null and state = 'succeeded'"
        " returning program_id", (action_id,))
    row = one(cur)
    if row is None:
        return Decimal("0")
    return meter(cur, tenant, kind=STEP_KIND,
                 program_id=str(row["program_id"]) if row["program_id"] else None)


def close_period(cur, tenant: dict[str, Any], period_id: str) -> dict[str, Any]:
    """Turn a period into a statement. Idempotent: a closed period is returned.

    Seats are counted from live API keys rather than from a number somebody
    typed. It is the closest thing this runtime has to a person, and a seat
    count nobody maintains is a seat count that undercharges forever.
    """
    cur.execute("select * from billing_period where id = %s", (period_id,))
    period = one(cur)
    if period is None:
        raise BillingError(f"no period {period_id} for this tenant")
    if period["closed_at"] is not None:
        return period

    cur.execute("select count(*) as n from api_key where revoked_at is null")
    seats_used = int(cur.fetchone()["n"])

    negotiated = _terms(tenant) if period["plan"] == NEGOTIATED else None
    closing = statement(plan_key=period["plan"],
                        consumed_credits=Decimal(str(period["consumed_credits"])),
                        seats_used=seats_used, negotiated=negotiated)
    import json

    cur.execute(
        "update billing_period set closed_at = now(), seats_used = %s,"
        " statement = %s where id = %s returning *",
        (seats_used, json.dumps(closing.as_dict()), period_id))
    return one(cur)


def cost_per_contact(cur, program_id: str | None = None,
                     since=None, until=None) -> dict[str, Any]:
    """Token spend per contact reached, against `docs/08`'s target (AGENT-4).

    The numerator is the model kinds only and it is `cost_micros` — what the
    calls cost this business — rather than `billed_credits`, which is what the
    customer is charged. The document targets the first; the second would
    report the price list back to itself.

    The denominator counts distinct people, not touches and not enrolments: a
    contact reached three times in a programme is one contact touched. A sent
    touch that names nobody predates `touch.person_id` and is excluded, which
    understates the denominator and so overstates the cost — the direction that
    cannot make a missed target look met.
    """
    from zolts import agentcost

    window = " and occurred_at >= %s" if since else ""
    window += " and occurred_at < %s" if until else ""
    spend_args: list[Any] = [sorted(agentcost.MODEL_KINDS), program_id, program_id]
    spend_args += [a for a in (since, until) if a]
    cur.execute(
        "select coalesce(sum(cost_micros), 0) as micros, count(*) as calls"
        "  from cost_event"
        " where kind = any(%s)"
        "   and (%s::uuid is null or program_id = %s::uuid)" + window,
        tuple(spend_args))
    spend = cur.fetchone()

    touch_window = " and t.sent_at >= %s" if since else ""
    touch_window += " and t.sent_at < %s" if until else ""
    reach_args: list[Any] = [program_id, program_id]
    reach_args += [a for a in (since, until) if a]
    cur.execute(
        "select count(distinct t.person_id) as contacts,"
        "       count(*) filter (where t.person_id is null) as unattributed"
        "  from touch t"
        "  join enrollment e on e.id = t.enrollment_id"
        " where t.sent_at is not null and t.direction = 'out'"
        "   and (%s::uuid is null or e.program_id = %s::uuid)" + touch_window,
        tuple(reach_args))
    reach = cur.fetchone()

    contacts = int(reach["contacts"])
    micros = int(spend["micros"])
    per_contact = round(micros / 1_000_000 / contacts, 4) if contacts else None
    return {
        "eurPerContact": per_contact,
        "targetEurPerContact": agentcost.TARGET_EUR_PER_CONTACT,
        "verdict": agentcost.judge(per_contact).value,
        "contactsTouched": contacts,
        "modelCalls": int(spend["calls"]),
        "tokenSpendEur": round(micros / 1_000_000, 4),
        # Reported rather than hidden: a denominator missing rows is a number
        # that reads high, and the reader is owed the reason.
        "touchesNamingNobody": int(reach["unattributed"]),
    }
