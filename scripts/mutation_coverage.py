#!/usr/bin/env python3
"""Measure what share of mutations the tests catch. A number, not a list.

`scripts/mutation_check.py` breaks fifteen named guards on every push and
requires a test to notice. That is regression-proofing a curated list: it says
nothing about the code it does not name, and `docs/24` has kept VER-1 open for
the real number since it was written.

This generates mutants across a package, applies them, runs the tests, and
reports the fraction caught. Three rules make the number worth reading:

* **It only reports a target whose tests it ran.** `zolts/` is pure and the
  database-free suite covers it in twenty seconds; `runtime/` is covered by
  tests that need Postgres, and a run that skipped them would report the
  runtime as uncovered when it is not. Naming a target runs the suite that
  could catch it, or the script refuses.
* **A survivor is named.** `file:line`, the operator, and what it became — a
  survival rate with no list is a number nobody can act on.
* **It is a sample, and says so.** The full mutant space is thousands of runs;
  the default is a seeded sample with a Wilson interval, and the report carries
  the sample size beside the rate so nobody quotes the point estimate alone.

    python3 scripts/mutation_coverage.py                    # zolts, 40 mutants
    python3 scripts/mutation_coverage.py --sample 8         # what CI runs
    python3 scripts/mutation_coverage.py --target runtime --sample 12

An equivalent mutant — one that changes the source and cannot change behaviour
— survives and is counted as survived. That is the conservative direction: it
makes the measured coverage lower than the truth rather than higher.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import os
import random
import signal
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Which tests could catch a mutation in each target, and how long that costs.
# A target whose suite is not run is a target this script will not report on.
TARGETS = {
    "zolts": {
        "package": "zolts",
        # The reference core is pure, so the database-free suite is the whole
        # of what could catch it.
        "pytest": ["-m", "not db"],
        "note": "the database-free suite, which is what covers a pure package",
    },
    "runtime": {
        "package": "runtime",
        # Most of the runtime's behaviour is only reachable with Postgres.
        "pytest": [],
        "note": "the whole suite, because the runtime's tests need a database",
    },
}

# Operator swaps, in both directions. Comparison and boolean operators are the
# classic mutation set: they are where an off-by-one guard and an inverted
# condition live, and both are defects this repository has actually had.
COMPARISONS = {
    ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
    ast.Eq: "==", ast.NotEq: "!=", ast.Is: "is", ast.IsNot: "is not",
    ast.In: "in", ast.NotIn: "not in",
}
SWAP = {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "==",
        "is": "is not", "is not": "is", "in": "not in", "not in": "in"}


@dataclass(frozen=True)
class Mutant:
    path: Path
    line: int
    start: int          # absolute offset in the file
    end: int
    was: str
    becomes: str
    kind: str

    @property
    def where(self) -> str:
        return f"{self.path.relative_to(ROOT)}:{self.line}"

    def as_dict(self) -> dict[str, object]:
        return {"where": self.where, "kind": self.kind,
                "was": self.was, "becomes": self.becomes}


def _offsets(source: str) -> list[int]:
    """Absolute offset of the first character of each line, 1-indexed."""
    out, total = [0, 0], 0
    for line in source.splitlines(keepends=True):
        total += len(line)
        out.append(total)
    return out


def _mutants_in(path: Path) -> list[Mutant]:
    """Every mutation this script knows how to make in one file.

    Edits are exact source spans rather than a re-render of the parsed tree:
    unparsing would drop every comment in the file, and this repository has
    tests that read source text — a mutant that failed one of those would be
    counted as caught for the wrong reason.
    """
    source = path.read_text()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    starts = _offsets(source)
    found: list[Mutant] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = node.left, node.comparators[0]
            if left.end_lineno != right.lineno:
                continue  # the operator spans lines; skip rather than guess
            begin = starts[left.end_lineno] + left.end_col_offset
            finish = starts[right.lineno] + right.col_offset
            text = source[begin:finish]
            token = text.strip()
            if token not in SWAP:
                continue
            lead = len(text) - len(text.lstrip())
            found.append(Mutant(path=path, line=left.end_lineno,
                                start=begin + lead, end=begin + lead + len(token),
                                was=token, becomes=SWAP[token], kind="comparison"))
        elif isinstance(node, ast.BoolOp) and len(node.values) >= 2:
            first, second = node.values[0], node.values[1]
            if first.end_lineno != second.lineno:
                continue
            begin = starts[first.end_lineno] + first.end_col_offset
            finish = starts[second.lineno] + second.col_offset
            text = source[begin:finish]
            token = text.strip()
            if token not in ("and", "or"):
                continue
            lead = len(text) - len(text.lstrip())
            found.append(Mutant(path=path, line=first.end_lineno,
                                start=begin + lead, end=begin + lead + len(token),
                                was=token, becomes="or" if token == "and" else "and",
                                kind="boolean"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, int) \
                and not isinstance(node.value, bool):
            if node.lineno != node.end_lineno:
                continue
            begin = starts[node.lineno] + node.col_offset
            finish = starts[node.end_lineno] + node.end_col_offset
            text = source[begin:finish]
            # Only plain decimal literals: `0x10`, `1_000` and a negative sign
            # belong to the parent node, and rewriting them here would produce
            # a mutant that is a syntax error rather than a behaviour change.
            if not text.isdigit():
                continue
            found.append(Mutant(path=path, line=node.lineno, start=begin, end=finish,
                                was=text, becomes=str(int(text) + 1), kind="constant"))
    return found


def _candidates(package: str) -> list[Mutant]:
    out: list[Mutant] = []
    for path in sorted((ROOT / package).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        out.extend(_mutants_in(path))
    return out


def _dirty() -> bool:
    result = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                            capture_output=True, text=True).stdout.splitlines()
    return any(not line.startswith("??") for line in result)


def _caught(pytest_args: list[str]) -> bool:
    """True when the tests failed, which under a mutant is the good outcome."""
    env = {**os.environ, "PYTHONPATH": "."}
    env.setdefault("ZOLTS_SECRET_KEY", "mutation-coverage-secret")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-x", "-q", "--no-header",
         "-p", "no:cacheprovider", "-p", "no:randomly", *pytest_args],
        cwd=ROOT, capture_output=True, text=True, env=env)
    return result.returncode != 0


def _wilson(caught: int, total: int) -> tuple[float, float]:
    """A 95% interval for the caught share. A point estimate off a sample of
    forty is a number that reads as precise and is not."""
    if not total:
        return (0.0, 0.0)
    z, p, n = 1.959963985, caught / total, total
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    spread = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)
    return (round(max(0.0, centre - spread), 4), round(min(1.0, centre + spread), 4))


@contextmanager
def _restored_on_signal(path: Path, original: str):
    """Put the file back if this process is asked to stop.

    `finally` handles an exception; a SIGTERM or a ctrl-c between the write and
    the restore does not reach it, and what is left behind is a source file
    with a mutation in it. A run of this script left exactly that in the tree.
    """
    def restore(signum, frame):  # pragma: no cover - exercised by killing a run
        path.write_text(original)
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    previous = {sig: signal.signal(sig, restore)
                for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def measure(target: str, sample: int, seed: int) -> dict[str, object]:
    spec = TARGETS[target]
    every = _candidates(spec["package"])
    chosen = random.Random(seed).sample(every, min(sample, len(every)))

    caught: list[Mutant] = []
    survived: list[Mutant] = []
    for index, mutant in enumerate(chosen, start=1):
        original = mutant.path.read_text()
        mutated = original[:mutant.start] + mutant.becomes + original[mutant.end:]
        # A `finally` covers an exception and not a signal, and a long run is
        # exactly the thing somebody interrupts: killing one mid-mutant left a
        # source file mutated in this repository's own working tree. The
        # handler restores before dying, and dies rather than swallowing it.
        with _restored_on_signal(mutant.path, original):
            mutant.path.write_text(mutated)
            try:
                bitten = _caught(spec["pytest"])
            finally:
                mutant.path.write_text(original)
        (caught if bitten else survived).append(mutant)
        print(f"  {index}/{len(chosen)} {'caught  ' if bitten else 'SURVIVED'} "
              f"{mutant.where} {mutant.was} -> {mutant.becomes}", file=sys.stderr)

    low, high = _wilson(len(caught), len(chosen))
    return {
        "target": target,
        "suiteRun": spec["note"],
        "mutantsAvailable": len(every),
        "sampled": len(chosen),
        "seed": seed,
        "caught": len(caught),
        "survived": len(survived),
        "caughtShare": round(len(caught) / len(chosen), 4) if chosen else None,
        "confidence95": [low, high],
        # Named, because a survival rate with no list is a number nobody can
        # act on. Each of these is a line a test could be written against.
        "survivors": [m.as_dict() for m in survived],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", choices=sorted(TARGETS), default="zolts")
    parser.add_argument("--sample", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20260908)
    parser.add_argument("--allow-dirty", action="store_true",
                        help="only for a checkout that is already disposable")
    args = parser.parse_args()

    if _dirty() and not args.allow_dirty:
        print("::error::the working tree is dirty. This script edits source files and "
              "restores them; run it on a clean tree so a crash cannot lose work.",
              file=sys.stderr)
        return 2

    print(f"measuring {args.target} against {TARGETS[args.target]['note']}…",
          file=sys.stderr)
    report = measure(args.target, args.sample, args.seed)
    print(json.dumps(report, indent=2))

    share = report["caughtShare"]
    if share is None:
        print("::error::no mutants were generated, so nothing was measured", file=sys.stderr)
        return 2
    low, high = report["confidence95"]
    print(f"::notice::{report['caught']}/{report['sampled']} mutants caught in "
          f"{args.target} ({share:.0%}, 95% CI {low:.0%}-{high:.0%}), sampled from "
          f"{report['mutantsAvailable']}", file=sys.stderr)
    # Deliberately not a gate. A sampled rate moves between runs, and a build
    # that fails on it would be a build that teaches the team to raise the
    # threshold. The number belongs in `docs/22`, where it can be argued with.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
