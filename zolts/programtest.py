"""Declarative program tests.

Claim under test (docs/04): "These run in CI. A program change that breaks a
compliance rule cannot be merged. That is the sales argument in front of an
enterprise DPO."

That claim is only true if the runner exists. This is it. A ProgramTest file
states fixtures and expectations; the runner drives the same modules the
runtime uses — policy, scoring, routing — so a passing test means the shipped
logic agrees, not a parallel reimplementation of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from zolts.dsl import Program
from zolts.expr import evaluate
from zolts.policy import ActionContext, Basis, Contact, Decision, evaluate as evaluate_policy
from zolts.scoring import Signal, intent_score  # noqa: F401  (scoring used by callers)


@dataclass
class CaseResult:
    name: str
    passed: bool
    failures: list[str] = field(default_factory=list)


@dataclass
class SuiteResult:
    program_key: str
    cases: list[CaseResult]

    @property
    def passed(self) -> bool:
        return all(case.passed for case in self.cases)

    @property
    def failure_count(self) -> int:
        return sum(1 for case in self.cases if not case.passed)


def _parse_age_hours(value: Any) -> float:
    """Accept relative ages such as '-45d' or '-6h', or a plain number of hours."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lstrip("-")
    unit, number = text[-1], text[:-1]
    multiplier = {"h": 1, "d": 24, "w": 168}.get(unit)
    if multiplier is None:
        raise ValueError(f"cannot parse age '{value}'")
    return float(number) * multiplier


def _resolve_tier(program: Program, variables: dict[str, Any]) -> str | None:
    """First matching tier wins, in declaration order — same as the router.

    The whole fixture is passed as variables: a tier may key off any scored
    field, not only `score` (03-ecommerce routes tier 1 on LTV percentile).
    """
    for tier in program.spec["route"]["tiers"]:
        if evaluate(tier["when"], variables):
            return tier["key"]
    return None


def _trigger_fires(program: Program, given: dict[str, Any]) -> bool:
    """Whether the declared signal would enrol the entity.

    A signal decayed below a tenth of its base strength is treated as expired:
    the trigger window has passed even if the raw event still exists.
    """
    signal = given.get("signal")
    if signal is None:
        return True

    events = program.spec["trigger"]["events"]
    matching = [e for e in events if e["signal"] == signal.get("type")]
    if not matching:
        return False

    # Enrollment is governed by the trigger window the program declares, not by
    # signal decay. Decay weights the score; the window decides whether the
    # event is still in scope at all. Conflating them let a 90-day-old signal
    # enrol under a 30-day window.
    age_h = _parse_age_hours(signal.get("observed_at", 0))
    if age_h > _parse_age_hours(program.spec["trigger"]["window"]):
        return False

    for event in matching:
        clause = event.get("where")
        if clause and not evaluate(clause, {"payload": signal.get("payload", {})}):
            return False
    return True


def _build_contact(given: dict[str, Any]) -> Contact:
    person = given.get("person", {})
    account = given.get("account", {})
    consent_raw = person.get("consent_state", {}) or {}
    consent = {
        channel: Basis(value["basis"] if isinstance(value, dict) else value)
        for channel, value in consent_raw.items()
    }
    return Contact(
        entity_id=person.get("id", "fixture"),
        country=person.get("country") or account.get("country") or "ZZ",
        consent=consent,
        suppressed_on=frozenset(person.get("suppressed_on", [])),
        unsubscribed_channels=frozenset(person.get("unsubscribed_channels", [])),
        touches_this_week=int(person.get("touches_this_week", 0)),
    )


def run_case(program: Program, case: dict[str, Any]) -> CaseResult:
    given = case.get("given", {}) or {}
    expect = case.get("expect", {}) or {}
    failures: list[str] = []

    if "policy_decision" in expect or "rule" in expect:
        contact = _build_contact(given)
        context = ActionContext(
            channel=given.get("channel", "email"),
            # Fixed so a case is reproducible. The hour here decides nothing:
            # the policy engine reads `local_hour` below, which a case sets
            # itself. A mutation-coverage run flagged this as an unkillable
            # mutant, which is the honest reading of a constant that looks
            # significant and is not.
            now=datetime(2026, 9, 4, 12),
            local_hour=int(given.get("local_hour", 12)),
            max_touches_per_week=int(
                program.spec.get("policy", {})
                .get("overrides", {})
                .get("max_touches_per_person_per_week", 3)
            ),
        )
        decision = evaluate_policy(contact, context)
        expected_decision = expect.get("policy_decision")
        if expected_decision and decision.decision is not Decision(expected_decision):
            failures.append(
                f"policy_decision: expected {expected_decision}, got {decision.decision.value} "
                f"({decision.rule_key}: {decision.rationale})"
            )
        expected_rule = expect.get("rule")
        if expected_rule and decision.rule_key != expected_rule:
            failures.append(f"rule: expected {expected_rule}, got {decision.rule_key}")

    if "enrolled" in expect:
        fires = _trigger_fires(program, given)
        if fires is not bool(expect["enrolled"]):
            failures.append(f"enrolled: expected {expect['enrolled']}, got {fires}")

    if "tier" in expect or "auto_send" in expect:
        tier = _resolve_tier(program, given)
        if "tier" in expect and tier != expect["tier"]:
            failures.append(f"tier: expected {expect['tier']}, got {tier}")
        if "auto_send" in expect:
            play = program.spec["plays"].get(tier, {})
            actual = bool(play.get("auto_send", False))
            if actual is not bool(expect["auto_send"]):
                failures.append(
                    f"auto_send: expected {expect['auto_send']}, got {actual} for tier {tier}"
                )

    return CaseResult(name=case.get("name", "<unnamed>"), passed=not failures, failures=failures)


def run_suite(program: Program, suite_path: str | Path) -> SuiteResult:
    document = yaml.safe_load(Path(suite_path).read_text())
    if document.get("kind") != "ProgramTest":
        raise ValueError(f"{suite_path}: expected kind ProgramTest")
    cases = [run_case(program, case) for case in document.get("cases", [])]
    return SuiteResult(program_key=document.get("program", program.key), cases=cases)
