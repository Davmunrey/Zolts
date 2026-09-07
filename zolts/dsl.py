"""Program loading, schema validation and content hashing."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
import yaml

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "examples" / "schema" / "zolts-program.schema.json"
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class ProgramError(ValueError):
    pass


@dataclass(frozen=True)
class Program:
    key: str
    version: str
    spec: dict[str, Any]
    raw: dict[str, Any]

    @property
    def spec_hash(self) -> str:
        """Stable hash of the compiled spec, independent of key ordering."""
        canonical = json.dumps(self.spec, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    @property
    def semver(self) -> tuple[int, int, int]:
        match = _SEMVER.match(self.version)
        if not match:
            raise ProgramError(f"version '{self.version}' is not semver")
        return tuple(int(g) for g in match.groups())  # type: ignore[return-value]

    @property
    def holdout_pct(self) -> float:
        return float(self.spec["experiment"]["holdout_pct"])


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text())


def load(path: str | Path) -> Program:
    """Load and schema-validate a program file."""
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ProgramError(f"{path}: expected a mapping at the document root")

    # YAML 1.1 coerces `on`/`off`/`yes`/`no` to booleans. If a trigger arrived
    # with a boolean key, someone reintroduced `on:` — fail loudly rather than
    # let schema validation report a confusing "missing events".
    trigger = raw.get("spec", {}).get("trigger", {})
    if isinstance(trigger, dict) and any(isinstance(k, bool) for k in trigger):
        raise ProgramError(
            f"{path}: trigger contains a boolean key — YAML 1.1 coerced `on:`; use `events:`"
        )

    jsonschema.Draft202012Validator(load_schema()).validate(raw)
    metadata = raw["metadata"]
    return Program(key=metadata["key"], version=metadata["version"], spec=raw["spec"], raw=raw)


_ONE_TO_ONE_BLUEPRINTS = {
    "b2b-saas-sales-led", "b2b-enterprise", "services-agency",
    "fintech-regulated", "healthtech-lifesci", "industrial-b2b",
}


def _is_one_to_one_motion(program: Program) -> bool:
    """Whether the top tier is expected to be human-reviewed.

    Unknown blueprints default to True: assuming human review is the safe
    failure mode, since the cost of a wrong auto-send exceeds the cost of an
    unnecessary review.
    """
    blueprint = program.raw.get("metadata", {}).get("blueprint")
    if blueprint is None:
        return True
    return blueprint in _ONE_TO_ONE_BLUEPRINTS


def lint(program: Program) -> list[str]:
    """Static checks the schema cannot express. Returns findings, empty if clean."""
    findings: list[str] = []
    spec = program.spec

    experiment = spec["experiment"]
    if experiment["holdout_pct"] < 5 and not experiment.get("holdout_waiver_reason"):
        findings.append("holdout below 5% without a recorded waiver reason")

    tiers = {tier["key"] for tier in spec["route"]["tiers"]}
    human_reviewed_top_tier = _is_one_to_one_motion(program)
    for tier_key, play in spec["plays"].items():
        if tier_key not in tiers:
            findings.append(f"play '{tier_key}' has no matching route tier")
        # The top tier of a 1:1 motion is the human-reviewed one. This does not
        # generalise to high-volume B2C, where the top tier means high LTV
        # rather than human-touched, so the rule is scoped by blueprint.
        if play.get("auto_send") and tier_key == "t1" and human_reviewed_top_tier:
            findings.append("tier 1 of a 1:1 motion must never auto-send")
        # This one holds everywhere: unattended sending without an eval gate is
        # the failure mode that produces brand incidents.
        if play.get("auto_send") and not play.get("auto_send_requires", {}).get("eval_score"):
            findings.append(f"play '{tier_key}' auto-sends without an eval_score threshold")

    for tier in tiers:
        if tier not in spec["plays"]:
            findings.append(f"route tier '{tier}' has no play defined; accounts would enter a dead end")

    if not any(exit_rule.get("suppress") for exit_rule in spec["exit"]):
        findings.append("no exit rule sets suppress: opt-outs would not be recorded")

    budget = spec["budget"]
    if budget.get("on_exceed") == "continue_and_alert" and not budget.get("max_cost_per_account"):
        findings.append("budget continues past its ceiling with no per-account cap")

    return findings


# The parameters an operator tunes, and what each one costs to get wrong. Every
# other field in the schema describes what a program *is*; these are the dials
# that move money and risk on a live one, which is why they are the set a
# console may edit without a deploy.
#
# Only the paths are named here. Type, bounds and choices are read out of the
# schema itself, because a control that restated them would eventually offer a
# value the engine rejects — and the point of ADR-002 is that there is one
# document and one validator, not a UI with its own opinion.
TUNABLE: tuple[tuple[str, str], ...] = (
    ("spec.experiment.holdout_pct",
     "Share held back to measure lift. Smaller detects less; larger costs sends."),
    ("spec.score.floor",
     "Minimum score to enrol. Raising it enrols fewer and better."),
    ("spec.trigger.window",
     "How long two signals may be apart and still combine."),
    ("spec.trigger.dedupe.cooldown",
     "How long before the same subject may be enrolled again."),
    ("spec.route.strategy",
     "Who receives the enrolled account."),
    ("spec.budget.monthly_credits",
     "Ceiling on what this program may spend in a billing period."),
    ("spec.budget.on_exceed",
     "What the runtime does when the ceiling is reached."),
    ("spec.policy.overrides.max_touches_per_person_per_week",
     "Contact pressure. Stricter than the tenant policy is allowed; looser is not."),
    ("spec.enrich.account.max_cost_per_account",
     "Ceiling the waterfall may spend resolving one account."),
    ("spec.enrich.person.max_cost_per_contact",
     "Ceiling the waterfall may spend resolving one contact."),
    ("spec.enrich.person.accuracy_sla",
     "Accuracy the waterfall must meet, which decides which vendors it may use."),
)


def _resolve(schema: dict[str, Any], path: str) -> dict[str, Any]:
    """Walk a dotted path into the schema, following one level of $ref.

    A path that does not resolve is a programming error, not a missing value: a
    control whose field the schema does not describe would render with no
    bounds at all and accept anything.
    """
    node: dict[str, Any] = schema
    for part in path.split("."):
        if "$ref" in node:
            ref = node["$ref"].split("/")[-1]
            node = (schema.get("$defs") or schema.get("definitions") or {})[ref]
        properties = node.get("properties")
        if not properties or part not in properties:
            raise ProgramError(f"the schema describes no field at '{path}'")
        node = properties[part]
    if "$ref" in node:
        ref = node["$ref"].split("/")[-1]
        node = (schema.get("$defs") or schema.get("definitions") or {})[ref]
    return node


def controls() -> list[dict[str, Any]]:
    """The tunable parameters, each carrying the schema's own constraints.

    What a caller renders from this cannot offer a value the schema rejects,
    because the bounds are the schema's and are read at call time rather than
    copied into a second place.
    """
    schema = load_schema()
    found = []
    for path, note in TUNABLE:
        node = _resolve(schema, path)
        control: dict[str, Any] = {"path": path, "note": note,
                                   "type": node.get("type", "string")}
        for key in ("minimum", "maximum", "enum", "pattern"):
            if key in node:
                control[key] = node[key]
        found.append(control)
    return found
