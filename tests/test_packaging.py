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

from tests.conftest import requires_db

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

    # `docs/24` quotes both figures in its status table and drifted twice
    # before it was checked here: the packaging test covered the two documents
    # a contributor reads and not the one an investor does.
    for document in (root / "README.md", root / "docs" / "20-runtime.md",
                     root / "docs" / "24-backlog.md"):
        text = document.read_text()
        quoted = {int(n) for n in re.findall(r"(\d{3,4}) tests", text)}
        quoted |= {int(n) for n in re.findall(r"\| Tests \| (\d{3,4}) \|", text)}
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


def test_the_deploy_either_builds_with_its_dependencies_or_does_not_build():
    """Two ways to be right and one way to be broken.

    Either the host rebuilds the site and installs what the build imports, or
    it does not build and serves a directory this repository has committed and
    CI keeps in sync. What cannot stand is a build command that runs the
    generator without its dependencies — which shipped, and failed the deploy
    while every check in CI was green, because CI installs those dependencies
    globally as its first step and so was structurally unable to notice.
    """
    import json
    import subprocess

    root = Path(__file__).resolve().parent.parent
    config = json.loads((root / "vercel.json").read_text())
    command = config.get("buildCommand") or ""
    output = config.get("outputDirectory")

    if "build_site" in command:
        needed = {DISTRIBUTIONS[m] for m in _third_party_imports(
            root / "scripts" / "build_site.py", root)}
        assert needed, "the site build imports nothing third-party; check the walker"
        missing = {d for d in needed if d not in command}
        assert not missing, (
            f"vercel.json builds the site without installing {sorted(missing)}. "
            f"CI installs them globally and will not catch this; the deploy will")
        return

    # No build, so the committed output is what the world gets.
    assert output, "vercel.json neither builds nor names an output directory"
    tracked = subprocess.run(["git", "ls-files", output], cwd=root,
                             capture_output=True, text=True).stdout.split()
    assert tracked, (
        f"vercel.json serves '{output}' without building it, and nothing in "
        f"'{output}' is committed. The deploy would serve an empty directory")
    assert f"{output}/index.html" in tracked, (
        f"'{output}' is committed without an index.html to serve")


# -- the schema the documents describe ------------------------------------

# Not tenant-scoped, and each for its own reason: `tenant` is the table the
# isolation is keyed on, `invitation` exists before its tenant does,
# `schema_migration` is the migrator's own bookkeeping, and `worker_heartbeat`
# is written by a tick that spans every tenant and read by an operator.
UNSCOPED = {"tenant", "invitation", "schema_migration", "worker_heartbeat"}


@requires_db
def test_the_documents_do_not_overstate_the_schema(db):
    """README said 18 tables with RLS forced on 16. There were 30 and 27.

    Nobody wrote it dishonestly; a number in a document is not re-measurable,
    so it is never re-measured — the same failure as the test counts, one layer
    down, and in the claim a security reviewer reads first.
    """
    import re

    with db.admin_tx() as cur:
        cur.execute(
            "select relname, relforcerowsecurity from pg_class"
            " where relnamespace = 'public'::regnamespace and relkind = 'r'")
        tables = {r["relname"]: r["relforcerowsecurity"] for r in cur.fetchall()}

    unforced = {name for name, forced in tables.items() if not forced}
    assert unforced == UNSCOPED, (
        f"a tenant-scoped table without forced RLS is a tenant reading another "
        f"tenant's rows: {sorted(unforced - UNSCOPED)}")

    total, scoped = len(tables), len(tables) - len(UNSCOPED)
    root = Path(__file__).resolve().parent.parent
    for document in (root / "README.md", root / "docs" / "20-runtime.md"):
        text = document.read_text()
        assert re.search(rf"\b{total} tables\b", text), (
            f"{document.name} does not say there are {total} tables")
        assert re.search(rf"\b(on all )?{scoped}\b", text), (
            f"{document.name} does not say RLS is forced on {scoped} of them")


@requires_db
def test_the_app_role_may_use_every_sequence(db):
    """A `bigserial` column is insertable only by a role that may call
    `nextval` on its sequence, and sequences are separate objects with their
    own privileges.

    `dossier.seq` is the first one in this schema — every primary key here is a
    uuid — and its insert failed on a permission nobody had granted because
    nobody knew to. The next sequence added must not repeat that.
    """
    with db.admin_tx() as cur:
        cur.execute("select sequencename from pg_sequences where schemaname = 'public'")
        sequences = [r["sequencename"] for r in cur.fetchall()]
        cur.execute(
            "select s.sequencename from pg_sequences s"
            " where s.schemaname = 'public'"
            "   and not has_sequence_privilege('zolts_app',"
            "         format('public.%I', s.sequencename), 'USAGE')")
        ungranted = [r["sequencename"] for r in cur.fetchall()]

    assert sequences, "the premise moved: this schema has no sequences at all"
    assert not ungranted, (
        f"the role that serves tenant requests cannot use {ungranted}; every "
        f"insert into the owning table fails")


def test_adr_003_does_not_claim_a_warehouse_the_runtime_does_not_read():
    """ADR-003 was written in the present tense — "Zolts materialises only what
    execution requires" — and the word `warehouse` appears nowhere in the
    runtime. What ships copies contacts and accounts into Zolts's own Postgres.

    A buyer's security review reads that ADR and asks to see the connector.
    This guard runs in both directions: while nothing reads a warehouse the
    document must say so, and the day something does, the document is stale
    and this fails until it is corrected.
    """
    import re
    from pathlib import Path

    runtime = " ".join(p.read_text(encoding="utf-8")
                       for p in Path("runtime").rglob("*.py"))
    implemented = bool(re.search(r"\bwarehouse\b|\bsnowflake\b|\bbigquery\b",
                                 runtime, re.I))
    adr = Path("docs/02-architecture.md").read_text(encoding="utf-8")
    block = adr[adr.index("**ADR-003 ·"):]
    block = block[:block.index("**ADR-004")]
    disclaimed = "not what ships" in block or "not yet implementation" in block

    if implemented:
        assert not disclaimed, (
            "the runtime reads a warehouse now; ADR-003 still says it does not")
    else:
        assert disclaimed, (
            "ADR-003 claims zero-copy over a warehouse in the present tense and "
            "nothing in runtime/ reads one. The document is corrected, never "
            "the measurement")


# -- the second host ships what the first one does --------------------------

def _brace_expand(pattern: str) -> list[str]:
    """`{a/**,b/*}` into its alternatives. One level, which is what Vercel's
    own example uses."""
    if not (pattern.startswith("{") and pattern.endswith("}")):
        return [pattern]
    return [p.strip() for p in pattern[1:-1].split(",")]


def _glob_matches(pattern: str, relative: str) -> bool:
    import re

    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.fullmatch(out, relative) is not None


def _vercel() -> dict:
    import json

    return json.loads((ROOT / "vercel.json").read_text())


@pytest.mark.parametrize("what", sorted(_runtime_data_paths()))
def test_the_vercel_function_ships_every_path_the_runtime_reads(what):
    """The Dockerfile test, for the second host. `excludeFiles` trims the
    bundle, and a pattern that catches `blueprints/` ships a function that
    starts, reports healthy and seeds a tenant with nothing (ADR-041)."""
    path = _runtime_data_paths()[what]
    relative = path.relative_to(ROOT).as_posix()
    excluded = _vercel()["functions"]["api/index.py"].get("excludeFiles", "")
    for pattern in _brace_expand(excluded):
        assert not _glob_matches(pattern, relative) and not _glob_matches(
            pattern, relative + "/x"), (
            f"vercel.json excludes '{pattern}' from the function, which drops {what} "
            f"({relative}). The function would start and behave as though the "
            f"data did not exist")


def test_the_root_requirements_are_the_runtimes():
    """Vercel installs the root `requirements.txt` and nothing else. Two lists
    would drift, and the one that drifts is the one that ships."""
    root = (ROOT / "requirements.txt").read_text()
    assert "-r runtime/requirements.txt" in root, (
        "the root requirements.txt does not include runtime/requirements.txt; "
        "the function would build without the runtime's dependencies")


def test_every_cron_points_at_a_route_the_function_mounts():
    """A cron whose path 404s is a worker that never runs, reported by nothing
    but the outbox thirty minutes after the first action is due."""
    from fastapi import FastAPI

    from runtime import serverless
    from runtime.config import Settings

    app = FastAPI()
    serverless.mount(app, db=object(), settings=Settings(
        database_url="postgresql://x", app_database_url="postgresql://y",
        secret_key="k", environment="test", lease_seconds=60, worker_batch=25,
        dry_run=True, agents_enabled=False))
    mounted = {getattr(route, "path", None) for route in app.routes}
    for cron in _vercel()["crons"]:
        assert cron["path"] in mounted, (
            f"vercel.json schedules {cron['path']} and the function mounts no such "
            f"route; the cron would 404 every minute")
    assert {"/api/tick", "/api/watch"} <= {c["path"] for c in _vercel()["crons"]}, (
        "the worker or the watcher is not scheduled; on this host nothing else runs it")


def test_every_path_the_runtime_answers_reaches_the_function():
    """Static files first, then everything else to the function. Without the
    rewrite `/health` is a 404 from the CDN and the console is unreachable."""
    rewrites = _vercel().get("rewrites") or []
    assert any(r["source"] == "/(.*)" and r["destination"] == "/api/index"
               for r in rewrites), (
        "vercel.json does not rewrite the paths the static site cannot answer "
        "to api/index; /console, /health and /v1/* would 404")


def test_the_static_policy_does_not_reach_the_served_console():
    """The static build closes `connect-src`; the served console fetches.
    A header rule for `/(.*)` that carries the static policy breaks the
    product to protect the brochure."""
    for rule in _vercel()["headers"]:
        names = {h["key"] for h in rule["headers"]}
        if "Content-Security-Policy" in names:
            assert rule["source"] in {"/", "/data/(.*)"}, (
                f"the static Content-Security-Policy is applied to {rule['source']}, "
                f"which the served console also matches")


def test_production_is_released_only_through_the_workflow_that_migrates():
    """The release command, on a host that has none (ADR-041).

    Two facts make one design: the platform's own deploys of `main` are off,
    and the workflow that replaces them migrates and runs preflight before it
    deploys. Either without the other is a push that goes around the checks
    a Fly release cannot skip.
    """
    import yaml

    enabled = _vercel().get("git", {}).get("deploymentEnabled")
    assert isinstance(enabled, dict) and enabled.get("main") is False, (
        "vercel.json lets the platform deploy main on push, around the migration "
        "and the preflight")

    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "deploy-vercel.yml").read_text())
    steps = workflow["jobs"]["release"]["steps"]
    runs = [step.get("run", "") for step in steps]

    def position(fragment: str) -> int:
        matches = [i for i, run in enumerate(runs) if fragment in run]
        assert matches, f"no step in the release job runs {fragment!r}"
        return matches[0]

    migrate = position("runtime.cli migrate")
    preflight = position("runtime.cli preflight")
    deploy = position("vercel@latest deploy --prod")
    smoke = position("smoke_deployed.py")
    assert migrate < preflight < deploy < smoke, (
        "the release job must migrate, then preflight, then deploy, then open the "
        f"URL; it runs them in the order {sorted([migrate, preflight, deploy, smoke])}")
    assert "ZOLTS_ENV: production" in (ROOT / ".github" / "workflows" / "deploy-vercel.yml").read_text(), (
        "preflight runs outside production mode, so a published key would pass")
