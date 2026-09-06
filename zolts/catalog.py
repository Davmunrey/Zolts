"""The join between programs and blueprints.

Programs and blueprints are authored in separate files and nothing in either
schema can see the other, so every cross-reference between them is unchecked
by construction: a program can name a blueprint that does not exist, trigger on
a signal its blueprint never arms, or quietly drop a suppression list its
blueprint requires. All three fail silently and all three are compliance
failures rather than typos.

This module performs the join and reports what does not line up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from zolts.blueprint import Blueprint, load_blueprints
from zolts.dsl import Program, load
from zolts.overlay import Layer, PolicyLoosened, resolve

PROGRAM_DIR = Path(__file__).resolve().parent.parent / "examples" / "programs"


@dataclass(frozen=True)
class Issue:
    program: str
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.program}: {self.kind} — {self.detail}"


@dataclass
class Catalog:
    programs: list[Program] = field(default_factory=list)
    blueprints: dict[str, Blueprint] = field(default_factory=dict)

    def blueprint_for(self, program: Program) -> Blueprint | None:
        return self.blueprints.get(program.raw.get("metadata", {}).get("blueprint", ""))

    @property
    def planned_programs(self) -> set[str]:
        """Programs a blueprint promises but that no file implements yet."""
        declared: set[str] = set()
        for blueprint in self.blueprints.values():
            declared |= set(blueprint.programs)
        return declared - {p.key for p in self.programs}


def load_catalog(program_dir: Path | None = None) -> Catalog:
    source = program_dir or PROGRAM_DIR
    if not source.is_dir():
        raise FileNotFoundError(
            f"program directory {source} does not exist; the package or image "
            "was built without it")
    programs = [load(p) for p in sorted(source.glob("*.yaml"))]
    return Catalog(programs=programs, blueprints={b.key: b for b in load_blueprints()})


def _program_signals(program: Program) -> set[str]:
    return {e["signal"] for e in program.spec["trigger"]["events"]}


def integrity(catalog: Catalog) -> list[Issue]:
    """Every cross-reference that neither schema can check. Empty means clean."""
    issues: list[Issue] = []

    for program in catalog.programs:
        key = program.key
        declared = program.raw.get("metadata", {}).get("blueprint")

        if not declared:
            issues.append(Issue(key, "no-blueprint",
                                "program declares no blueprint, so it inherits no policy"))
            continue

        blueprint = catalog.blueprints.get(declared)
        if blueprint is None:
            issues.append(Issue(key, "unknown-blueprint",
                                f"references '{declared}', which does not exist"))
            continue

        if key not in blueprint.programs:
            issues.append(Issue(key, "not-declared",
                                f"blueprint '{declared}' does not list this program"))

        unarmed = sorted(_program_signals(program) - set(blueprint.signals))
        if unarmed:
            issues.append(Issue(key, "unarmed-signal",
                                f"triggers on {unarmed}, which '{declared}' does not arm"))

        # The overlay rule, applied for real: a program may tighten its
        # blueprint's policy and may never loosen it.
        overrides = program.spec.get("policy", {}).get("overrides", {})
        if overrides:
            try:
                resolve([
                    Layer(f"blueprint:{declared}", {"policy": blueprint.policy}),
                    Layer(f"program:{key}", {"policy": overrides}),
                ])
            except PolicyLoosened as exc:
                issues.append(Issue(key, "policy-loosened", str(exc)))

        floor = blueprint.spec.get("default_holdout_pct", 0)
        if program.holdout_pct < floor:
            issues.append(Issue(
                key, "holdout-below-blueprint",
                f"holdout {program.holdout_pct}% is under the {floor}% "
                f"'{declared}' sets as its default",
            ))

    return issues
