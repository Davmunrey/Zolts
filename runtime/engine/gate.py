"""The policy gate.

Product invariant 3: every external action records a policy decision, allow or
deny, with a reason. The gate runs at dispatch time rather than at plan time,
because consent, suppression and frequency all change between the moment a step
is scheduled and the moment it would be sent — which is exactly the window in
which someone unsubscribes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from runtime import policy_packs
from runtime.repo import entities, ledger
from zolts import policy

# Contact-local hour is a property of the contact, not the server. Until the
# runtime resolves a real timezone per contact this is a coarse offset by
# country, which is honest about being coarse rather than defaulting to UTC and
# quietly calling 03:00 local a reasonable time to telephone someone.
_UTC_OFFSET = {
    "ES": 1, "FR": 1, "DE": 1, "NL": 1, "IT": 1, "PT": 0, "GB": 0, "IE": 0,
    "US": -5, "CA": -5, "MX": -6, "BR": -3, "AU": 10, "SG": 8, "IN": 5,
}


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    decision: str
    rule_key: str
    rationale: str
    jurisdiction: str | None
    decision_id: str
    # Whether this contact's jurisdiction requires a generated message to carry
    # an AI-disclosure marker. It rides on the gate result rather than being
    # resolved again at generation, because the gate is the one place that
    # reads the *published* pack — a second lookup would be a second answer the
    # day an operator publishes their own (D-90, decision 55).
    requires_ai_disclosure: bool = False


def _basis(value: Any) -> policy.Basis | None:
    try:
        return policy.Basis(value)
    except ValueError:
        return None


def local_hour(country: str | None, now: datetime) -> int:
    """The hour on the contact's clock, for the quiet-hours rule.

    Public because the preview evaluates the same rule for the same contact
    without recording a decision, and a second copy of this arithmetic is the
    hour the two would disagree on.
    """
    return (now.hour + _UTC_OFFSET.get(country or "", 0)) % 24


def build_contact(cur, person: dict[str, Any], touches_week: int) -> policy.Contact:
    consent_state = person.get("consent_state") or {}
    consent: dict[str, policy.Basis] = {}
    unsubscribed: set[str] = set()
    for channel, record in consent_state.items():
        if not isinstance(record, dict):
            continue
        if record.get("opted_out"):
            unsubscribed.add(channel)
            continue
        basis = _basis(record.get("basis"))
        if basis is not None:
            consent[channel] = basis

    email = person.get("email")
    domain = str(email).split("@")[-1] if email else None
    scopes = entities.suppressed_keys(cur, str(email) if email else None, domain)
    # The engine's suppression scopes are named for what they are; the policy
    # pack names jurisdictional registries. Both must deny, so both are passed.
    suppressed = set(scopes)
    for key, present in ((k, k in scopes) for k in ("email", "domain")):
        if present:
            suppressed.update({"robinson_list_es", "robinson_list", "global_suppression"})

    return policy.Contact(
        entity_id=str(person["id"]),
        country=person.get("country") or "US",
        consent=consent,
        suppressed_on=frozenset(suppressed),
        unsubscribed_channels=frozenset(unsubscribed),
        touches_this_week=touches_week,
    )


def check(cur, tenant_id: str, *, person: dict[str, Any], channel: str,
          enrollment_id: str | None, program_spec: dict[str, Any],
          action_cost_eur: float = 0.0, remaining_budget_eur: float = float("inf"),
          now: datetime | None = None) -> GateResult:
    now = now or datetime.now(timezone.utc)
    # The person, not the enrolment. An enrolment is on an account in every
    # shipped programme, so counting by it made the per-person cap a
    # per-account-per-programme one (D-92).
    touches_week = ledger.touches_this_week(cur, str(person["id"]))
    contact = build_contact(cur, person, touches_week)

    overrides = (program_spec.get("policy") or {}).get("overrides") or {}
    cap = int(overrides.get("max_touches_per_person_per_week", 3))

    context = policy.ActionContext(
        channel=channel, now=now, local_hour=local_hour(contact.country, now), max_touches_per_week=cap,
        remaining_budget_eur=remaining_budget_eur, action_cost_eur=action_cost_eur)

    # The program's own policy block, applied to the jurisdiction's rule. The
    # schema has always allowed four overrides and the runtime read one: a
    # program declaring stricter quiet hours sent at three in the morning, and
    # one naming an extra suppression list did not check it.
    # The published pack, not the dict in `zolts/policy.py`. A deployment with
    # no active pack refuses rather than deciding under rules nobody can name
    # (D-53), and the decision cites the digest of the rules that produced it.
    pack_row = policy_packs.active(cur)
    verdict = policy.evaluate(contact, context, pack=policy_packs.rules_of(pack_row),
                              overrides=overrides)
    decision_id = ledger.record_decision(
        cur, tenant_id, subject_type="person", subject_id=str(person["id"]),
        action=f"{channel}.send", decision=verdict.decision.value,
        rule_key=verdict.rule_key, jurisdiction=verdict.jurisdiction,
        rationale=verdict.rationale, pack_version=pack_row["version"],
        pack_digest=pack_row["digest"])

    return GateResult(
        allowed=verdict.allowed, decision=verdict.decision.value,
        rule_key=verdict.rule_key, rationale=verdict.rationale,
        jurisdiction=verdict.jurisdiction, decision_id=decision_id,
        requires_ai_disclosure=policy.disclosure_required(
            contact.country, policy_packs.rules_of(pack_row), overrides))
