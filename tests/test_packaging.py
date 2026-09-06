"""What the image ships versus what the runtime reads.

Every one of these paths was absent from the image once. The container started,
answered `/health` with `"status": "ok"`, seeded a tenant with zero programs and
reported success, and `/console` returned 500. Nothing failed; the product was
simply empty. A test that greps a Dockerfile is unglamorous, and it is the only
thing standing between that and a first-run demo.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"

# Resolved from the modules rather than written down here, so renaming a
# directory in the code and forgetting the Dockerfile fails this test.
def _runtime_data_paths() -> dict[str, Path]:
    from runtime.api.app import SURFACE
    from runtime.db import MIGRATIONS
    from zolts.blueprint import BLUEPRINT_DIR
    from zolts.catalog import PROGRAM_DIR
    from zolts.dsl import SCHEMA_PATH as PROGRAM_SCHEMA
    from zolts.mapping import SCHEMA_PATH as MAPPING_SCHEMA

    return {"blueprints": BLUEPRINT_DIR, "programs": PROGRAM_DIR,
            "program schema": PROGRAM_SCHEMA, "mapping schema": MAPPING_SCHEMA,
            "console surface": SURFACE, "migrations": MIGRATIONS}


def _copied_sources() -> list[str]:
    """The left-hand side of every COPY in the Dockerfile."""
    sources = []
    for line in DOCKERFILE.read_text().splitlines():
        match = re.match(r"\s*COPY\s+(\S+)\s+(\S+)\s*$", line)
        if match:
            sources.append(match.group(1).rstrip("/"))
    return sources


@pytest.mark.parametrize("what", sorted(_runtime_data_paths()))
def test_every_path_the_runtime_reads_is_in_the_image(what):
    path = _runtime_data_paths()[what]
    assert path.exists(), f"{what} does not exist in the repository: {path}"

    relative = path.relative_to(ROOT).as_posix()
    copied = _copied_sources()
    assert any(relative == source or relative.startswith(source + "/")
               for source in copied), (
        f"the runtime reads {what} from {relative}, which no COPY in the "
        f"Dockerfile ships. The image would start, report healthy, and behave "
        f"as though the data did not exist. Copied: {copied}")


def test_a_missing_blueprint_directory_is_an_error_not_an_empty_list(tmp_path):
    """Globbing an absent directory returns nothing, which reads as 'this
    product has no blueprints'. That is how the empty image reported success."""
    from zolts.blueprint import load_blueprints

    with pytest.raises(FileNotFoundError, match="does not exist"):
        load_blueprints(tmp_path / "not-here")

    (tmp_path / "empty").mkdir()
    assert load_blueprints(tmp_path / "empty") == []


def test_a_missing_program_directory_is_an_error_not_an_empty_catalog(tmp_path):
    from zolts.catalog import load_catalog

    with pytest.raises(FileNotFoundError, match="does not exist"):
        load_catalog(tmp_path / "not-here")


# -- the numbers the documents quote about the tests themselves ----------

def test_the_documents_do_not_overstate_the_test_suite():
    """README claimed 676 tests and 302 against Postgres. The real figures were
    689 and 187 — the second overstated by 60% in a document investors read.

    Nobody wrote it dishonestly; the number was simply never re-measurable, so
    it was never re-measured. This test makes it measurable, and the cost of
    keeping it true is one edit in the pull request that changes it.
    """
    import re
    import subprocess
    import sys

    root = Path(__file__).resolve().parent.parent
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q", "-p",
         "no:cacheprovider"],
        cwd=root, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": "."})
    total = int(re.search(r"(\d+) tests collected", collected.stdout).group(1))

    needing_db = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q", "-m", "db",
         "-p", "no:cacheprovider"],
        cwd=root, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": "."})
    against_postgres = int(re.search(r"(\d+)/\d+ tests collected", needing_db.stdout).group(1))

    for document in (root / "README.md", root / "docs" / "20-runtime.md"):
        text = document.read_text()
        quoted = {int(n) for n in re.findall(r"(\d{3,4}) tests", text)}
        assert quoted, f"{document.name} no longer quotes a test count"
        assert quoted == {total}, (
            f"{document.name} says {sorted(quoted)} tests and there are {total}. "
            f"The document is corrected, never the measurement")
        assert re.search(rf"\b{against_postgres}\b", text), (
            f"{document.name} does not state that {against_postgres} tests run "
            f"against a real Postgres")


# -- the deploy path installs what the build imports ---------------------

# Module name to the distribution that provides it. Two entries, and both are
# here because a module and its package share a name only by convention.
DISTRIBUTIONS = {"yaml": "pyyaml", "jsonschema": "jsonschema"}


def _third_party_imports(entry: Path, root: Path) -> set[str]:
    """Top-level third-party modules reachable from a script in this repo."""
    import ast

    seen: set[Path] = set()
    found: set[str] = set()
    queue = [entry]
    local = {p.stem for p in (root / "scripts").glob("*.py")}
    while queue:
        path = queue.pop()
        if path in seen or not path.exists():
            continue
        seen.add(path)
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                if top in ("zolts", "runtime"):
                    queue += list((root / top).glob("*.py"))
                elif top in local:
                    queue.append(root / "scripts" / f"{top}.py")
                elif top in DISTRIBUTIONS:
                    found.add(top)
    return found


def test_the_vercel_build_installs_what_the_site_build_imports():
    """CI installs these globally, so CI can never notice their absence here.

    That is exactly how the first deploy failed with ModuleNotFoundError while
    every check was green: the build command ran `python3 scripts/build_site.py`
    on an image that had no pyyaml. A test that reads the deploy configuration
    is the only place this shows up before a deploy does.
    """
    import json

    root = Path(__file__).resolve().parent.parent
    command = json.loads((root / "vercel.json").read_text()).get("buildCommand", "")
    assert "build_site.py" in command, "vercel.json no longer builds the site"

    needed = {DISTRIBUTIONS[m] for m in _third_party_imports(
        root / "scripts" / "build_site.py", root)}
    assert needed, "the site build imports nothing third-party; check the walker"
    missing = {d for d in needed if d not in command}
    assert not missing, (
        f"vercel.json builds the site without installing {sorted(missing)}. "
        f"CI installs them globally and will not catch this; the deploy will")
