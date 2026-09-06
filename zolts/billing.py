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


# What additional credits cost, in euros, from `docs/12`: "1 credit ~ EUR 0.01.
# Volume pricing: 0-100k -> EUR 0.010; 100k-500k -> EUR 0.008; >500k -> EUR 0.006."
#
# Not chosen here. The document priced this before the runtime existed and the
# runtime simply never charged it; a test re-reads the ladder out of `docs/12`
# so the two cannot drift apart quietly.
#
# The ladder is **graduated and measured over the overage alone**: the first
# 100,000 credits beyond the plan cost EUR 0.010 each, the next 400,000 cost
# EUR 0.008, the rest EUR 0.006. The document does not say which of two readings
# it means, and the other one is worse:
#
#   * Graduated over the overage (this) is monotone. Consuming one more credit
#     always costs at least as much as consuming one fewer.
#   * A flat rate chosen by total consumption has a cliff at every boundary. A
#     tenant crossing 100,000 would see the whole bill reprice downward, so
#     spending more money would lower the invoice. That is not a discount, it
#     is a defect, and a test pins the monotonicity that rules it out.
#
# Ambiguity resolved toward the customer and toward the property that can be
# tested. Registered as decision 17 rather than left in a comment.
CREDIT_TIERS: tuple[tuple[Decimal | None, Decimal], ...] = (
    (Decimal("100000"), Decimal("0.010")),
    (Decimal("500000"), Decimal("0.008")),
    (None, Decimal("0.006")),
)

# The list price, for anything that needs one number: the first tier.
EUR_PER_CREDIT = CREDIT_TIERS[0][1]

# An additional operator seat, per month, also from `docs/12`. Seats were
# counted and not charged for exactly as long as credits were.
EUR_PER_SEAT = Decimal("90")

CENTS = Decimal("0.01")


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    platform_eur: Decimal
    seats: int
    credits: Decimal
    # Per-contract overrides. None means the list price above. An enterprise
    # contract that negotiated its own ladder carries it here rather than
    # having the standard one applied to terms nobody agreed.
    credit_tiers: tuple[tuple[Decimal | None, Decimal], ...] | None = None
    seat_eur: Decimal | None = None


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


def check_tiers(tiers: tuple[tuple[Decimal | None, Decimal], ...]) -> None:
    """A ladder that is not ascending and open-ended prices some volume twice or
    not at all. Refuse it here rather than discovering it on an invoice."""
    if not tiers:
        raise BillingError("a credit ladder needs at least one band")
    if tiers[-1][0] is not None:
        raise BillingError(
            "the last band must be open-ended (upper bound None), or consumption "
            "above it has no price")
    previous = Decimal("0")
    for upper, rate in tiers[:-1]:
        if upper is None:
            raise BillingError("only the last band may be open-ended")
        if upper <= previous:
            raise BillingError(f"band boundaries must ascend; {upper} follows {previous}")
        previous = upper
    for _, rate in tiers:
        if rate < 0:
            raise BillingError("a negative rate pays the customer to consume")


def price_credits(credits: Decimal | float,
                  tiers: tuple[tuple[Decimal | None, Decimal], ...] | None = None
                  ) -> Decimal:
    """What a volume of additional credits costs, graduated across the ladder.

    Graduated, not flat: each band prices only the credits that fall inside it.
    The alternative — one rate chosen by total volume — makes the bill fall as
    consumption rises at every boundary.
    """
    ladder = tiers or CREDIT_TIERS
    check_tiers(ladder)
    remaining_credits = Decimal(str(credits))
    if remaining_credits <= 0:
        return Decimal("0.00")

    total = Decimal("0")
    floor = Decimal("0")
    for upper, rate in ladder:
        band = remaining_credits if upper is None else min(remaining_credits, upper - floor)
        total += band * rate
        remaining_credits -= band
        if remaining_credits <= 0:
            break
        floor = upper  # type: ignore[assignment]
    return total.quantize(CENTS, rounding=ROUND_HALF_UP)


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
    credit_tiers: tuple[tuple[Decimal | None, Decimal], ...] = CREDIT_TIERS
    seat_eur: Decimal = EUR_PER_SEAT

    @property
    def overage_eur(self) -> Decimal:
        return price_credits(self.overage_credits, self.credit_tiers)

    @property
    def seats_eur(self) -> Decimal:
        return (Decimal(self.seats_over) * self.seat_eur).quantize(
            CENTS, rounding=ROUND_HALF_UP)

    @property
    def total_eur(self) -> Decimal:
        return self.platform_eur + self.overage_eur + self.seats_eur

    @property
    def blended_eur_per_credit(self) -> Decimal | None:
        """What the overage worked out at, per credit. None when there is none.

        Reported because the ladder is graduated: a tenant who sees a rate of
        EUR 0.008 in the price list and EUR 0.0094 on the invoice has not been
        overcharged, and the number that explains it belongs on the statement
        rather than in a support conversation.
        """
        if self.overage_credits <= 0:
            return None
        return (self.overage_eur / self.overage_credits).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP)

    @property
    def within_plan(self) -> bool:
        return self.overage_credits == 0 and self.seats_over == 0

    def as_dict(self) -> dict[str, object]:
        blended = self.blended_eur_per_credit
        return {
            "plan": self.plan,
            "platformEur": float(self.platform_eur),
            "includedCredits": float(self.included_credits),
            "consumedCredits": float(self.consumed_credits),
            "overageCredits": float(self.overage_credits),
            "seatsIncluded": self.seats_included,
            "seatsUsed": self.seats_used,
            "seatsOver": self.seats_over,
            "overageEur": float(self.overage_eur),
            "seatEur": float(self.seat_eur),
            "seatsEur": float(self.seats_eur),
            "totalEur": float(self.total_eur),
            "blendedEurPerCredit": None if blended is None else float(blended),
            "creditTiers": [
                {"upTo": None if upper is None else float(upper),
                 "eurPerCredit": float(rate)}
                for upper, rate in self.credit_tiers],
            "withinPlan": self.within_plan,
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
        seats_used=seats_used, seats_over=seats_over,
        credit_tiers=(plan.credit_tiers or CREDIT_TIERS),
        seat_eur=(plan.seat_eur if plan.seat_eur is not None else EUR_PER_SEAT))


def remaining(plan_key: str, consumed_credits: Decimal | float,
              negotiated: Plan | None = None) -> Decimal:
    """Credits left in the period. Negative once the plan is exhausted."""
    plan = negotiated if plan_key == NEGOTIATED else plan_for(plan_key)
    if plan is None:
        raise BillingError("an enterprise tenant needs its negotiated terms")
    return plan.credits - Decimal(str(consumed_credits))
