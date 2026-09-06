"""What a period costs, and what a tenant is allowed to spend.

`cost_event.billed_credits` has existed since the first migration and no caller
has ever set it. The price list in `docs/12` is complete — eight billable
actions, four plans, a three-part tariff — and the runtime billed nothing. The
structure was there; it did not run.

This module is the reference for both. The document is the claim and this is
what a test measures it against, in that order: a price that lives in prose is
a price two people will read differently.

Pure arithmetic, no I/O, like everything else in `zolts/`. Which period a
tenant is in and how much they have consumed are questions for the runtime;
what it costs is a question for this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# Credits per billable action. Written as Decimal because 0.5 and 0.2 are
# prices: a tenth of a credit lost to float drift on a million signal checks is
# a real invoice being wrong.
CREDITS: dict[str, Decimal] = {
    "enrich.email": Decimal("8"),
    "enrich.phone": Decimal("25"),
    "enrich.firmographics": Decimal("4"),
    "signal.check": Decimal("0.5"),
    "agent.generate": Decimal("3"),
    "agent.dossier": Decimal("20"),
    "email.send": Decimal("1"),
    "program.step": Decimal("0.2"),
}


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    platform_eur: Decimal
    seats: int
    credits: Decimal


PLANS: dict[str, Plan] = {
    "starter": Plan("starter", "Starter", Decimal("490"), 2, Decimal("15000")),
    "growth": Plan("growth", "Growth", Decimal("1490"), 5, Decimal("60000")),
    "scale": Plan("scale", "Scale", Decimal("3900"), 12, Decimal("200000")),
}

# Enterprise is deliberately absent. Its platform fee, seats and credits are
# negotiated per contract, and a default here would be a number somebody
# eventually invoices. A tenant on `enterprise` carries its own terms.
NEGOTIATED = "enterprise"


class BillingError(ValueError):
    pass


def credits_for(kind: str, units: float | Decimal = 1) -> Decimal:
    """What one metered action costs, in credits.

    An unknown kind raises rather than defaulting to zero. A silent zero is how
    a whole category of usage becomes free without anybody deciding it should
    be, and the caller that meters it is the one place that can say what it is.
    """
    if kind not in CREDITS:
        raise BillingError(
            f"'{kind}' has no price; add it to CREDITS and to docs/12, or the "
            f"usage is free. Known: {', '.join(sorted(CREDITS))}")
    return (CREDITS[kind] * Decimal(str(units))).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP)


def plan_for(key: str) -> Plan | None:
    """The plan's terms, or nothing when they are negotiated."""
    if key == NEGOTIATED:
        return None
    plan = PLANS.get(key)
    if plan is None:
        raise BillingError(
            f"unknown plan '{key}'; known: {', '.join(sorted(PLANS))}, {NEGOTIATED}")
    return plan


@dataclass(frozen=True)
class Statement:
    """What a period comes to. Every figure is derived, none is stored twice."""
    plan: str
    platform_eur: Decimal
    included_credits: Decimal
    consumed_credits: Decimal
    overage_credits: Decimal
    seats_included: int
    seats_used: int
    seats_over: int
    # Deliberately not a euro figure. The overage rate is contractual and this
    # repository has never agreed one; inventing it here would produce an
    # invoice nobody signed. See the decision register.
    overage_rate_agreed: bool = False

    @property
    def within_plan(self) -> bool:
        return self.overage_credits == 0 and self.seats_over == 0

    def as_dict(self) -> dict[str, object]:
        return {
            "plan": self.plan,
            "platformEur": float(self.platform_eur),
            "includedCredits": float(self.included_credits),
            "consumedCredits": float(self.consumed_credits),
            "overageCredits": float(self.overage_credits),
            "seatsIncluded": self.seats_included,
            "seatsUsed": self.seats_used,
            "seatsOver": self.seats_over,
            "withinPlan": self.within_plan,
            "note": None if self.within_plan else
                    "This period exceeded the plan. The overage rate is "
                    "contractual and is not computed here.",
        }


def statement(*, plan_key: str, consumed_credits: Decimal | float,
              seats_used: int,
              negotiated: Plan | None = None) -> Statement:
    """Close a period into what it costs.

    A negotiated plan must supply its own terms. Falling back to a standard
    plan's numbers for an enterprise contract is the kind of mistake that is
    only discovered by the customer.
    """
    plan = negotiated if plan_key == NEGOTIATED else plan_for(plan_key)
    if plan is None:
        raise BillingError(
            "an enterprise tenant has negotiated terms; pass them as `negotiated` "
            "rather than billing them on a standard plan")

    consumed = Decimal(str(consumed_credits))
    overage = max(Decimal("0"), consumed - plan.credits)
    seats_over = max(0, seats_used - plan.seats)
    return Statement(
        plan=plan.key, platform_eur=plan.platform_eur,
        included_credits=plan.credits, consumed_credits=consumed,
        overage_credits=overage, seats_included=plan.seats,
        seats_used=seats_used, seats_over=seats_over)


def remaining(plan_key: str, consumed_credits: Decimal | float,
              negotiated: Plan | None = None) -> Decimal:
    """Credits left in the period. Negative once the plan is exhausted."""
    plan = negotiated if plan_key == NEGOTIATED else plan_for(plan_key)
    if plan is None:
        raise BillingError("an enterprise tenant needs its negotiated terms")
    return plan.credits - Decimal(str(consumed_credits))
