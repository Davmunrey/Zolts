#!/usr/bin/env python3
"""Run every declarative program test and report failures.

Wired into CI: a program change that breaks a compliance expectation fails the
build rather than reaching a prospect.
"""

from __future__ import annotations

import sys
from pathlib import Path

from zolts.dsl import load
from zolts.programtest import run_suite

PROGRAM_DIR = Path("examples/programs")
TEST_DIR = Path("examples/tests")


def main() -> int:
    suites = sorted(TEST_DIR.glob("*.test.yaml"))
    if not suites:
        print("no program tests found", file=sys.stderr)
        return 1

    total = failed = 0
    for suite_path in suites:
        program_path = PROGRAM_DIR / suite_path.name.replace(".test.yaml", ".yaml")
        if not program_path.exists():
            print(f"FAIL {suite_path.name}: no program at {program_path}", file=sys.stderr)
            failed += 1
            continue

        result = run_suite(load(program_path), suite_path)
        total += len(result.cases)
        failed += result.failure_count
        status = "OK  " if result.passed else "FAIL"
        print(f"{status} {suite_path.name}  ({len(result.cases)} cases)")
        for case in result.cases:
            if not case.passed:
                print(f"       - {case.name}")
                for failure in case.failures:
                    print(f"           {failure}")

    print(f"\n{total - failed}/{total} cases passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
