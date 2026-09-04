"""Policy engine: blocking pre-execution evaluation.

Claim under test (docs/11): every external action passes a jurisdictional
evaluation before it executes, and the default posture blocks rather than
permits. Packs are data, not code, so a regulatory change ships without a
deployment.

This is product logic, not legal advice. Packs require counsel validation
before production use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REVIEW = "review"


class Basis(str, Enum):
    CONSENT = "consent"
    LEGITIMATE_INTEREST = "legitimate_interest"
    CONTRACT = "contract"


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    rule_key: str
    rationale: str
    jurisdiction: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


@dataclass(frozen=True)
class JurisdictionRule:
    """What a jurisdiction requires, per channel.

    `blocked_by_default` encodes the conservative posture: where the legal
    position is contested (cold B2B email in Germany), the pack refuses and
    the customer must record an override. Blocking is recoverable; a fine is
    not.
    """
    country: str
    required_basis: dict[str, Basis]
    blocked_channels: frozenset[str] = frozenset()
    suppression_lists: tuple[str, ...] = ()
    quiet_hours: tuple[time, time] = (time(20, 0), time(8, 0))


@dataclass
class Contact:
    entity_id: str
    country: str
    consent: dict[str, Basis] = field(default_factory=dict)
    suppressed_on: frozenset[str] = frozenset()
    unsubscribed_channels: frozenset[str] = frozenset()
    touches_this_week: int = 0


@dataclass
class ActionContext:
    channel: str
    now: datetime
    local_hour: int
    max_touches_per_week: int = 3
    remaining_budget_eur: float = float("inf")
    action_cost_eur: float = 0.0
    overrides: frozenset[str] = frozenset()  # documented, recorded customer overrides


# Pack v1. Data, not code — see docs/11 for the sourcing and caveats.
PACK_V1: dict[str, JurisdictionRule] = {
    "ES": JurisdictionRule(
        country="ES",
        required_basis={
            "email": Basis.LEGITIMATE_INTEREST,
            "linkedin": Basis.LEGITIMATE_INTEREST,
            "voice": Basis.LEGITIMATE_INTEREST,
            "whatsapp": Basis.CONSENT,
        },
        suppression_lists=("robinson_list_es",),
    ),
    "DE": JurisdictionRule(
        country="DE",
        required_basis={"email": Basis.CONSENT, "linkedin": Basis.LEGITIMATE_INTEREST},
        blocked_channels=frozenset({"voice"}),
    ),
    "FR": JurisdictionRule(
        country="FR",
        required_basis={"email": Basis.LEGITIMATE_INTEREST, "voice": Basis.LEGITIMATE_INTEREST},
        suppression_lists=("bloctel",),
    ),
    "GB": JurisdictionRule(
        country="GB",
        required_basis={"email": Basis.LEGITIMATE_INTEREST, "voice": Basis.LEGITIMATE_INTEREST},
        suppression_lists=("tps", "ctps"),
    ),
    "US": JurisdictionRule(
        country="US",
        required_basis={"email": Basis.LEGITIMATE_INTEREST, "sms": Basis.CONSENT},
    ),
    "CA": JurisdictionRule(
        country="CA",
        required_basis={"email": Basis.CONSENT},
    ),
}

# An unknown jurisdiction is not an implicit allow. Consent is required until a
# pack exists, which is the whole point of a default-deny posture.
UNKNOWN_JURISDICTION = JurisdictionRule(
    country="__unknown__",
    required_basis={},
)


def rule_for(country: str, pack: dict[str, JurisdictionRule] | None = None) -> JurisdictionRule:
    return (pack or PACK_V1).get(country.upper(), UNKNOWN_JURISDICTION)


def evaluate(
    contact: Contact,
    context: ActionContext,
    pack: dict[str, JurisdictionRule] | None = None,
) -> PolicyDecision:
    """Evaluate one action. The first failing rule wins and is recorded.

    Order matters: opt-out and suppression are checked before anything else,
    because an unsubscribed contact must not be reachable even under a valid
    legal basis or a customer override.
    """
    rule = rule_for(contact.country, pack)
    channel = context.channel
    jurisdiction = rule.country

    if channel in contact.unsubscribed_channels:
        return PolicyDecision(Decision.DENY, "suppression.unsubscribed",
                              f"contact opted out of {channel}", jurisdiction)

    for list_key in rule.suppression_lists:
        if list_key in contact.suppressed_on:
            return PolicyDecision(Decision.DENY, f"suppression.{list_key}",
                                  f"contact present on {list_key}", jurisdiction)

    if channel in rule.blocked_channels and f"channel.{channel}" not in context.overrides:
        return PolicyDecision(Decision.DENY, f"jurisdiction.{jurisdiction}.channel_blocked",
                              f"{channel} is blocked by default in {jurisdiction}", jurisdiction)

    required = rule.required_basis.get(channel, Basis.CONSENT)
    held = contact.consent.get(channel)
    if held is None or not _basis_satisfies(held, required):
        override_key = f"basis.{channel}"
        if override_key in context.overrides:
            return PolicyDecision(Decision.REVIEW, f"jurisdiction.{jurisdiction}.basis_overridden",
                                  f"{channel} requires {required.value}; customer override recorded",
                                  jurisdiction)
        return PolicyDecision(Decision.DENY, f"jurisdiction.{jurisdiction}.basis_required",
                              f"{channel} requires {required.value}, contact holds "
                              f"{held.value if held else 'none'}", jurisdiction)

    if contact.touches_this_week >= context.max_touches_per_week:
        return PolicyDecision(Decision.DENY, "frequency.cap_reached",
                              f"{contact.touches_this_week} touches already this week "
                              f"(cap {context.max_touches_per_week})", jurisdiction)

    if _in_quiet_hours(context.local_hour, rule.quiet_hours) and channel in {"voice", "sms", "whatsapp"}:
        return PolicyDecision(Decision.DENY, "quiet_hours",
                              f"local hour {context.local_hour} falls in quiet hours", jurisdiction)

    if context.action_cost_eur > context.remaining_budget_eur:
        return PolicyDecision(Decision.DENY, "budget.exceeded",
                              f"action costs {context.action_cost_eur:.4f} EUR, "
                              f"{context.remaining_budget_eur:.4f} EUR remaining", jurisdiction)

    return PolicyDecision(Decision.ALLOW, "ok", "all checks passed", jurisdiction)


def _basis_satisfies(held: Basis, required: Basis) -> bool:
    """Consent and contract both satisfy a legitimate-interest requirement.

    The reverse never holds: legitimate interest does not stand in for consent.
    """
    if held is required:
        return True
    if required is Basis.LEGITIMATE_INTEREST:
        return held in {Basis.CONSENT, Basis.CONTRACT}
    return False


def _in_quiet_hours(local_hour: int, window: tuple[time, time]) -> bool:
    start, end = window[0].hour, window[1].hour
    if start > end:  # window crosses midnight
        return local_hour >= start or local_hour < end
    return start <= local_hour < end
