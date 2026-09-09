#!/usr/bin/env python3
"""Run the suite under varied ambient conditions and report what disagrees.

Five defects in `docs/22` were the same shape: **a check that fails on its
environment rather than on its subject.** A smoke test that failed one run in
ten on a hash (D-35). Preflight tests that inherited whatever the ambient
Postgres shipped (D-59). A demo that failed outside European working hours,
red about seventy per cent of the week for months (D-68). The verification
gate reporting eight broken guards on a machine with no database (D-71). Nine
tests — three of them tenant isolation — failing because the fixtures fell back
to a role that bypasses the grants they assert (D-73).

Every one was found the same way: run the thing twice under different ambient
conditions and read the *difference* rather than the second number. Every one
of those probes was ad hoc, and none of them is in the repository. That is the
position hand-verified guards were in before `mutation_check.py`, and it ended
the same way: the verification worked once and could not be repeated.

**This is not a test and it does not gate a push.** Each condition is a full
suite run of about three minutes, so the default pair takes six and the whole
set nearer fifteen. It is a deliberate command, like `mutation_check.py` was,
and what it reports is a *disagreement* — a test that passes under one ambient
condition and fails under another. A condition where everything agrees proves
the suite is indifferent to it, which is the property being checked.

    python3 scripts/ambient_check.py                 # baseline and one shift
    python3 scripts/ambient_check.py --all           # every condition
    python3 scripts/ambient_check.py --list          # what they are
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Condition:
    """One ambient state the suite is run under."""
    id: str
    why: str
    #: Environment overrides. A value of None removes the variable.
    env: dict[str, str | None] = field(default_factory=dict)
    #: Part of the default pair, or only under --all.
    default: bool = False


CONDITIONS: tuple[Condition, ...] = (
    Condition(
        id="baseline",
        why="the environment as configured, so everything else has something to differ from",
        default=True),
    Condition(
        id="clock-west",
        why=("ten hours behind UTC, which puts the local date a day earlier for most of "
             "the UTC day. D-68 was a send window closing at 18:00 Europe/Madrid: correct "
             "product behaviour, a red suite, and nobody noticed because everybody ran it "
             "inside European working hours"),
        env={"TZ": "Pacific/Honolulu"},
        default=True),
    Condition(
        id="clock-east",
        why="twelve hours ahead, the same question from the other side",
        env={"TZ": "Pacific/Auckland"}),
    Condition(
        id="operator-shell",
        why=("the variables somebody working on this box exports. A test that reads "
             "configuration from the environment behaves differently in a shell that has "
             "it set, and the shell is not part of the checkout"),
        env={"ZOLTS_SECRET_KEY": "an-operators-own-key", "ZOLTS_ENV": "production",
             "ZOLTS_DRY_RUN": "1", "ZOLTS_VERCEL_PLAN": "pro", "ZOLTS_WORKER_BATCH": "50"}),
    Condition(
        id="no-app-role",
        why=("the app pool falls back to the owner, who bypasses every grant. Nine tests "
             "asserted what the restricted role cannot do and all nine failed on correct "
             "code (D-73); they must now *skip*, and a failure here means the fallback is "
             "silently proving nothing again"),
        env={"ZOLTS_TEST_APP_DATABASE_URL": None}),
)

BY_ID = {c.id: c for c in CONDITIONS}
# A node id may carry a parametrised suffix containing spaces, so the id runs
# to the outcome word rather than to the first space.
_OUTCOME = re.compile(r"^(\S+::.+?) (PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)", re.M)


def _run(condition: Condition) -> dict[str, str]:
    """Every test's outcome under one condition, by node id.

    `-p no:cacheprovider` so one run cannot reorder the next, and `-v` because
    counts are what hid three of these five: a suite that fails two tests and
    skips two others reports the same total as one that passes all four.
    """
    env = {**os.environ, "PYTHONPATH": "."}
    for name, value in condition.env.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    result = subprocess.run(
        # `-v` and not `-q`: the per-test lines are the whole point, and `-q`
        # silently wins over `-v` — the first version of this script passed
        # both and matched nothing.
        [sys.executable, "-m", "pytest", "tests/", "-v", "--no-header",
         "-p", "no:cacheprovider", "--tb=no"],
        cwd=ROOT, capture_output=True, text=True, env=env)
    outcomes = dict(_OUTCOME.findall(result.stdout))
    if not outcomes:
        print(f"::error::{condition.id} collected nothing; the run itself failed",
              file=sys.stderr)
        print(result.stdout[-2000:], file=sys.stderr)
    return outcomes


def check(conditions: list[Condition]) -> int:
    baseline_id = conditions[0].id
    results: dict[str, dict[str, str]] = {}
    for condition in conditions:
        print(f"running under {condition.id} …", flush=True)
        results[condition.id] = _run(condition)
        if not results[condition.id]:
            return 2
        counts: dict[str, int] = {}
        for outcome in results[condition.id].values():
            counts[outcome] = counts.get(outcome, 0) + 1
        print(f"  {', '.join(f'{n} {k.lower()}' for k, n in sorted(counts.items()))}")

    base = results[baseline_id]
    # A verdict that moves *into or out of* failure is the defect: every one of
    # the five passed under one ambient state and failed under another. A test
    # that skips instead is the honest answer to a premise the condition
    # removed — `no-app-role` exists to produce exactly that — so it is
    # reported and not counted against the run. Removing the premise and then
    # failing is the thing this looks for.
    broke: list[tuple[str, str, str, str]] = []
    stood_down: list[tuple[str, str, str, str]] = []
    for condition in conditions[1:]:
        other = results[condition.id]
        for node in sorted(set(base) | set(other)):
            was, now = base.get(node, "ABSENT"), other.get(node, "ABSENT")
            if was == now:
                continue
            if "FAILED" in (was, now) or "ERROR" in (was, now):
                broke.append((condition.id, node, was, now))
            else:
                stood_down.append((condition.id, node, was, now))

    print()
    for where, node, was, now in stood_down:
        print(f"stands down  {node}\n             {baseline_id}: {was} → {where}: {now}")
    if stood_down:
        print(f"\n{len(stood_down)} tests answered a removed premise by skipping, which is "
              f"the correct answer and evidence the condition did something\n")

    if not broke:
        print(f"nothing broke across {len(conditions)} conditions: no test's *verdict* "
              f"depends on the ambient state")
        return 0

    for where, node, was, now in broke:
        print(f"DIFFERS {node}\n        {baseline_id}: {was} → {where}: {now}")
    print(f"\n{len(broke)} verdicts depend on the ambient state rather than on the code")
    print("::error::a test whose verdict moves with the environment is a test about the "
          "environment; see the five in docs/22 (D-35, D-59, D-68, D-71, D-73)",
          file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true", help="every condition, not the pair")
    parser.add_argument("--list", action="store_true", help="describe the conditions")
    parser.add_argument("--only", nargs="+", metavar="ID",
                        help="run these conditions; the first is the baseline")
    args = parser.parse_args()

    if args.list:
        for condition in CONDITIONS:
            mark = "*" if condition.default else " "
            print(f"{mark} {condition.id}\n    {condition.why}")
        print("\n* runs by default; --all runs the rest too")
        return 0

    if args.only:
        unknown = [i for i in args.only if i not in BY_ID]
        if unknown:
            print(f"::error::no condition named {', '.join(unknown)}", file=sys.stderr)
            return 2
        chosen = [BY_ID[i] for i in args.only]
    else:
        chosen = [c for c in CONDITIONS if args.all or c.default]
    if len(chosen) < 2:
        print("::error::a difference needs two conditions to be a difference",
              file=sys.stderr)
        return 2
    return check(chosen)


if __name__ == "__main__":
    raise SystemExit(main())
