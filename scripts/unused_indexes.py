#!/usr/bin/env python3
"""Which indexes does the whole test suite never once use?

An index is a standing cost — every insert and update maintains it — paid for a
read that may not exist. The cost is invisible: nothing fails, nothing is slow
enough to notice at test volume, and the migration that added it reads like
diligence. It is the repository's signature defect wearing a schema: a complete
specification with no caller (`docs/22`).

Static analysis cannot answer this. An index is chosen by the planner, never
named in a query, so "does any code use it" is not a question grep can settle.
Postgres already counts the answer. This resets the statistics, runs the suite
as the workload, and reads `pg_stat_user_indexes.idx_scan` back.

**Two kinds of zero, and only one is a finding.** An index backing a primary key
or a unique constraint earns its cost on *write*, by refusing a duplicate; zero
scans is its normal condition and it is reported separately, never flagged. A
plain index exists only to be read, so a plain index with zero scans across the
entire suite is either dead weight or a path no test walks. Both are worth
knowing and the report does not pretend to tell them apart.

**What this measurement cannot say.** Test tables hold tens of rows, and the
planner correctly prefers a sequential scan over an index on a table that small.
So a zero here is evidence that no test *needed* the index, not proof that
production would not use it. Read it as a question to answer, never as a verdict
to act on blindly — the honest direction of the error is that it over-reports.

    python3 scripts/unused_indexes.py               # reset, run suite, report
    python3 scripts/unused_indexes.py --no-run      # report on stats as they are
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Indexes whose zero is understood and accepted, with the reason. An entry here
# is a claim someone made in writing, not a silence — and every one of them
# should name what settles the question instead, because "we looked once" ages
# badly. Remove an entry and this reports the index again, which is the point.
ACCEPTED: dict[str, str] = {
    # The enrichment sweeps (D-60). `test_index_usage.py` reads the plan with
    # sequential scans disabled and asserts both that these are chosen and that
    # no `Sort` appears — a stronger claim than a scan count, and one that does
    # not depend on fixture size. EXPLAIN plans a query without running it, so
    # it moves no counter here.
    "person_email_missing_ix": "asserted by test_index_usage.py; EXPLAIN moves no counter",
    "person_phone_missing_ix": "asserted by test_index_usage.py; EXPLAIN moves no counter",
    "account_firmographics_missing_ix":
        "asserted by test_index_usage.py; EXPLAIN moves no counter",
    # Correct, and the suite is simply too small to make the planner want it:
    # the aggregate filters in `reporting.py` and `console.py` that match its
    # predicate run over tens of rows, where a sequential scan is the right
    # plan. Kept deliberately after review rather than dropped with the others.
    "outcome_unverified":
        "predicate matches the filters in reporting.py and console.py; test volume "
        "is too small for the planner to prefer it",
}


def _url() -> str:
    url = os.environ.get("ZOLTS_TEST_DATABASE_URL")
    if not url:
        raise SystemExit(
            "ZOLTS_TEST_DATABASE_URL is not set. This measures a real database; "
            "a coverage number produced without one would be a number about nothing.")
    return url


def _reset(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("select pg_stat_reset()")


def _run_suite() -> int:
    env = {**os.environ, "PYTHONPATH": "."}
    proc = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q",
                           "-p", "no:cacheprovider"],
                          cwd=ROOT, env=env, capture_output=True, text=True)
    tail = [line for line in proc.stdout.splitlines() if line.strip()][-1:]
    print(f"suite: {tail[0] if tail else 'no output'}")
    return proc.returncode


def _read(url: str) -> list[dict]:
    with psycopg.connect(url, autocommit=True) as conn:
        # Force the pending statistics out of the backends before reading them;
        # the collector is asynchronous and a read racing it under-reports.
        conn.execute("select pg_stat_force_next_flush()")
        rows = conn.execute("""
            select s.relname as table_name,
                   s.indexrelname as index_name,
                   s.idx_scan,
                   i.indisunique or i.indisprimary as backs_a_constraint,
                   pg_relation_size(s.indexrelid) as bytes
              from pg_stat_user_indexes s
              join pg_index i on i.indexrelid = s.indexrelid
             where s.schemaname = 'public'
             order by s.idx_scan, s.relname, s.indexrelname
        """).fetchall()
    return [{"table": r[0], "index": r[1], "scans": r[2],
             "constraint": r[3], "bytes": r[4]} for r in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-run", action="store_true",
                        help="read the statistics as they stand, without resetting or running")
    args = parser.parse_args(argv)

    url = _url()
    if not args.no_run:
        _reset(url)
        if _run_suite() != 0:
            print("the suite failed, so these counts describe a run that did not finish",
                  file=sys.stderr)

    rows = _read(url)
    if not rows:
        raise SystemExit("no indexes found; has the database been migrated?")

    plain_unused = [r for r in rows if r["scans"] == 0 and not r["constraint"]
                    and r["index"] not in ACCEPTED]
    accepted = [r for r in rows if r["scans"] == 0 and r["index"] in ACCEPTED]
    constraint_unused = [r for r in rows if r["scans"] == 0 and r["constraint"]]
    used = [r for r in rows if r["scans"] > 0]

    print(f"\n{len(rows)} indexes, {len(used)} used by the suite")
    print(f"{len(constraint_unused)} unused indexes back a key or a unique constraint "
          f"and earn their cost on write")
    if accepted:
        print(f"{len(accepted)} unused with a written reason:")
        for r in accepted:
            print(f"  {r['index']:<40} {ACCEPTED[r['index']]}")

    if not plain_unused:
        print("\nNo plain index went unused. Every index this schema declares was read "
              "at least once by the suite.")
        return 0

    print(f"\n{len(plain_unused)} plain index(es) the suite never used:\n")
    for r in plain_unused:
        print(f"  {r['index']}")
        print(f"    on {r['table']}, {r['bytes']} bytes")
    print("\nEach is a standing write cost for a read no test performs. Either a "
          "query is missing, a test is missing, or the index is.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
