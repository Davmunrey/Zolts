"""Policy engine: blocking pre-execution evaluation.

Claim under test (docs/11): every external action passes a jurisdictional
evaluation before it executes, and the default posture blocks rather than
permits. Packs are data, not code, so a regulatory change ships without a
deployment.

This is product logic, not legal advice. Packs require counsel validation
before production use.
"""

from __future__ import annotations

from dataclasses import dataclass, replace, field
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
    overrides: dict[str, Any] | None = None,
) -> PolicyDecision:
    """Evaluate one action. The first failing rule wins and is recorded.

    Order matters: opt-out and suppression are checked before anything else,
    because an unsubscribed contact must not be reachable even under a valid
    legal basis or a customer override.

    `overrides` is the program's own policy block, applied to the
    jurisdiction's rule before anything is evaluated. It may only tighten;
    `tighten` raises on anything that would relax the pack.
    """
    rule = tighten(rule_for(contact.country, pack), overrides or {})
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


class OverrideNotUnderstood(ValueError):
    """A program declared an override the runtime cannot interpret."""


# Kept as an alias: the first version of this refused a looser override, and
# the name is what callers still catch.
OverrideIsLooser = OverrideNotUnderstood


def _quiet_span(window: tuple[time, time]) -> int:
    """How many hours a quiet window covers, crossing midnight if it must."""
    start, end = window[0].hour, window[1].hour
    return (end - start) % 24 or 24


def tighten(rule: JurisdictionRule, overrides: dict[str, Any]) -> JurisdictionRule:
    """Apply a program's policy overrides, refusing any that loosen the rule.

    The schema has always said "Only overrides stricter than the tenant policy
    are accepted", and the runtime accepted none of them: the gate read
    `max_touches_per_person_per_week` and ignored `quiet_hours`,
    `channels_require_basis` and `lists_check`. A program declaring that it
    must not send between 21:00 and 08:00 sent at three in the morning, and a
    program naming an extra suppression list did not check it.

    Ignoring is the worst of the three possible behaviours. Honouring an
    override does what the operator asked; refusing it tells them it cannot be
    done; ignoring it lets them believe they are protected.

    **The stricter of the two always wins, per field and per jurisdiction.** A
    program cannot weaken the pack, and it does not need to be refused for
    trying: a program shipped for six countries states one baseline, and under
    a stricter jurisdiction the jurisdiction is what applies. The first version
    of this raised instead, and refused all four shipped programs — program 01
    declares `email: legitimate_interest`, which is the ES baseline and a
    relaxation of the DE and CA packs.

    So the operator is never less protected than they asked for, and never less
    protected than the law where the contact lives.

      quiet_hours              the longer of the two windows
      channels_require_basis   the basis that is harder to satisfy
      lists_check              the union; a program cannot drop one by omission

    A refusal is reserved for a declaration nobody can act on — a half-written
    window, a timezone nothing implements. Those are configuration mistakes,
    not relaxations.

    **Not the same question as `zolts.overlay`, which answers a similar one.**
    `overlay.resolve` merges the customer's own configuration layers —
    blueprint, industry pack, tenant, program — and there a lower layer that
    weakens an inherited policy is a hard error, because the layers are all the
    customer's and one of them contradicting another is a mistake somebody
    made.

    This function merges the customer's configuration with the *jurisdiction's*
    rule, which nobody in the customer's organisation authored. A program sold
    in six countries states one baseline, and under a stricter country the
    country wins without anybody having made a mistake. Raising there would
    reject correct programs, which is what the first version of this did.

    Two layers of the same shape, two different answers, for a reason worth
    keeping: a customer may not contradict themselves, and may not overrule
    the law.
    """
    if not overrides:
        return rule

    quiet = rule.quiet_hours
    declared = overrides.get("quiet_hours")
    if declared:
        # `start`/`end` is the vocabulary the programs and the schema use. The
        # schedule block says `opens`/`closes` for sending windows, and reading
        # one with the other's names is how this was written wrong the first
        # time: every shipped program was refused because none of them use the
        # words the code was looking for.
        start, end = declared.get("start"), declared.get("end")
        if not (start and end):
            raise OverrideIsLooser(
                "quiet_hours needs both `start` and `end`; a half-declared "
                f"window is not a window (got {sorted(declared)})")
        zone = declared.get("tz", "contact_local")
        if zone != "contact_local":
            # The gate derives the local hour from the contact's country. A
            # program naming another zone would be silently evaluated in the
            # contact's anyway, which is the ignoring this ADR exists to stop.
            raise OverrideIsLooser(
                f"quiet_hours tz '{zone}' is not implemented; quiet hours are "
                "evaluated in the contact's local time")
        window = (time.fromisoformat(start), time.fromisoformat(end))
        # The longer window. A program declaring a shorter quiet period than
        # the jurisdiction does not get more sending hours out of it.
        if _quiet_span(window) > _quiet_span(rule.quiet_hours):
            quiet = window

    basis = dict(rule.required_basis)
    for channel, name in (overrides.get("channels_require_basis") or {}).items():
        wanted = Basis(name)
        current = basis.get(channel)
        # Stricter means the current basis would no longer satisfy the new
        # requirement. Consent satisfies legitimate interest, so demanding
        # consent where the pack asks for legitimate interest is a tightening;
        # the reverse is not.
        # The harder of the two to satisfy. `_basis_satisfies(current, wanted)`
        # means the pack's requirement already meets the program's, so the
        # program is asking for no more than the jurisdiction and the
        # jurisdiction stands.
        if current is not None and _basis_satisfies(current, wanted):
            continue
        basis[channel] = wanted

    lists = tuple(rule.suppression_lists)
    for name in overrides.get("lists_check") or []:
        if name not in lists:
            lists = lists + (name,)

    return replace(rule, required_basis=basis, quiet_hours=quiet,
                   suppression_lists=lists)


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
