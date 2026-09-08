"""The measurement itself, checked. A coverage number nobody verified is worse
than none: it reads as evidence and is a script's opinion of itself.

`scripts/mutation_check.py` re-proves fifteen named guards; this file is about
`scripts/mutation_coverage.py`, which samples the mutant space and reports what
share the tests catch. What matters here is that its mutants are real — they
change the source, they parse, and the file comes back exactly as it was — and
that it refuses to report a target whose tests it did not run.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import mutation_coverage as coverage  # noqa: E402


def test_every_target_names_the_suite_that_could_catch_it():
    """`runtime/` is covered by tests that need Postgres. Measuring it with the
    database-free suite would report it as uncovered when it is not, which is
    a number that lies in the direction that flatters nobody and helps nobody.
    """
    assert set(coverage.TARGETS) == {"zolts", "runtime"}
    assert coverage.TARGETS["zolts"]["pytest"] == ["-m", "not db"]
    assert coverage.TARGETS["runtime"]["pytest"] == [], (
        "the runtime's mutants must be run against the whole suite")
    for spec in coverage.TARGETS.values():
        assert spec["note"], "a target that does not say which suite ran is not reportable"


def test_the_generator_finds_mutants_in_the_pure_core():
    mutants = coverage._candidates("zolts")
    assert len(mutants) > 100, "a generator that finds nothing measures nothing"
    kinds = {m.kind for m in mutants}
    assert kinds == {"comparison", "boolean", "constant"}


@pytest.mark.parametrize("package", ["zolts", "runtime"])
def test_every_mutant_produces_a_file_that_still_parses(package):
    """A mutant that is a syntax error is caught by every test for the wrong
    reason, and would inflate the measured coverage towards 100%."""
    by_file: dict[Path, list] = {}
    for mutant in coverage._candidates(package):
        by_file.setdefault(mutant.path, []).append(mutant)

    for path, mutants in by_file.items():
        source = path.read_text()
        # Ten per file is enough to catch a generator that produces broken
        # spans, and keeps this test in the seconds rather than the minutes.
        for mutant in mutants[:10]:
            mutated = source[:mutant.start] + mutant.becomes + source[mutant.end:]
            assert mutated != source, f"{mutant.where} changed nothing"
            assert source[mutant.start:mutant.end] == mutant.was, (
                f"{mutant.where} points at {source[mutant.start:mutant.end]!r}, "
                f"not at {mutant.was!r}")
            try:
                ast.parse(mutated)
            except SyntaxError as exc:  # pragma: no cover - the failure message
                raise AssertionError(f"{mutant.where} produced a syntax error: {exc}")


def test_a_mutation_run_restores_the_file_it_edited(tmp_path, monkeypatch):
    """It writes to the working tree. A crash between the write and the
    restore is what the dirty-tree refusal exists for; this is the happy path
    doing what it says."""
    target = ROOT / "zolts" / "experiment.py"
    before = target.read_text()

    monkeypatch.setattr(coverage, "_caught", lambda args: True)
    report = coverage.measure("zolts", sample=2, seed=7)

    assert target.read_text() == before, "a measured file was left mutated"
    assert report["sampled"] == 2 and report["caught"] == 2
    assert report["survivors"] == []


def test_a_survivor_is_named_where_a_test_could_be_written(monkeypatch):
    """A survival rate with no list is a number nobody can act on."""
    monkeypatch.setattr(coverage, "_caught", lambda args: False)
    report = coverage.measure("zolts", sample=3, seed=11)

    assert report["caught"] == 0 and report["survived"] == 3
    for survivor in report["survivors"]:
        path, line = survivor["where"].rsplit(":", 1)
        assert (ROOT / path).exists() and int(line) > 0
        assert survivor["was"] != survivor["becomes"]


def test_the_interval_is_wider_than_the_point_estimate():
    """Forty mutants is a sample. A point estimate quoted alone reads as
    precise and is not, so the report carries the interval beside it."""
    low, high = coverage._wilson(30, 40)
    assert low < 0.75 < high
    assert high - low > 0.15, "a 95% interval on forty observations is not narrow"
    assert coverage._wilson(0, 0) == (0.0, 0.0)


def test_the_measurement_is_reported_and_not_gated():
    """A build that failed on a sampled rate is a build that teaches the team
    to lower the threshold. The number belongs in `docs/22`, where it can be
    argued with."""
    source = (ROOT / "scripts" / "mutation_coverage.py").read_text()
    assert "Deliberately not a gate" in source
    workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text()
    assert "mutation_coverage.py --sample" in workflow, "CI never runs the sampler"


def test_an_interrupted_run_puts_the_file_back(tmp_path):
    """A `finally` covers an exception, not a signal. Killing a run of this
    script mid-mutant left `runtime/cli.py` mutated in this repository's own
    working tree, and the restore now happens on the way out of a signal too.

    Driven in a subprocess against a temporary file: the handler re-raises the
    signal after restoring, so calling it in-process would kill the test run,
    and pointing it at a real source file would risk leaving one mutated if
    this test itself were interrupted.
    """
    import signal
    import subprocess
    import sys
    import time

    target = tmp_path / "subject.py"
    target.write_text("ORIGINAL = 1\n")
    script = (
        "import sys, time;"
        f"sys.path.insert(0, {str(ROOT / 'scripts')!r});"
        "import mutation_coverage as c;"
        "from pathlib import Path;"
        f"p = Path({str(target)!r});"
        "original = p.read_text();"
        "ctx = c._restored_on_signal(p, original);"
        "ctx.__enter__();"
        "p.write_text('MUTATED = 2\\n');"
        "time.sleep(60)"
    )
    child = subprocess.Popen([sys.executable, "-c", script])
    try:
        deadline = time.monotonic() + 15
        while target.read_text() == "ORIGINAL = 1\n" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert target.read_text() == "MUTATED = 2\n", "the child never wrote the mutation"
        child.send_signal(signal.SIGTERM)
        child.wait(timeout=15)
    finally:
        if child.poll() is None:  # pragma: no cover - only if the wait failed
            child.kill()
    assert target.read_text() == "ORIGINAL = 1\n", (
        "an interrupted run left the file mutated")
