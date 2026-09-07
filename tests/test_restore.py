"""Run the restore, do not reason about it.

`scripts/restore.sh` ends with the line "a restore that has not been
preflighted is a backup nobody has tested", and the script itself had never
been executed. Two tests asserted the *reasoning* behind its first step — that
`pg_dump` emits `GRANT ... TO zolts_app` and no `CREATE ROLE`, so a restore
into a fresh project silently produces an application role with no privileges
— and nothing ran the four steps.

The first version of this file ran them and proved nothing. It restored into a
new database on the same cluster, where `zolts_app` already exists, so the
condition the script guards against never arose: deleting the whole
role-creation step left every test passing. Roles are cluster-wide, which is
the entire reason the failure exists, and a test that restores beside the
original database cannot see it.

These rewrite the dump so its GRANTs name a role this cluster does not have.
That is the production condition — a dump carried to a fresh managed project —
reproduced without a second cluster, and it is the condition under which
skipping step 1 loses every privilege in silence.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from tests.conftest import requires_db

RESTORE = Path("scripts/restore.sh")
PASSWORD = "restore-test-not-a-secret"


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}",
                       parts.query, parts.fragment))


def _as_role(url: str, role: str, password: str) -> str:
    parts = urlsplit(url)
    host = parts.netloc.split("@")[-1]
    return urlunsplit((parts.scheme, f"{role}:{password}@{host}",
                       parts.path, parts.query, parts.fragment))


@pytest.fixture
def restored(db, tmp_path):
    """A database restored from a dump whose grants name an absent role.

    Yields `(url, role, output)`. Both the database and the role are dropped
    afterwards, in that order — a role owning nothing still cannot be dropped
    while a database grants to it.
    """
    from tests.conftest import OWNER_URL

    if not RESTORE.exists():
        pytest.skip("scripts/restore.sh is absent")

    dump = tmp_path / "dump.sql"
    made = subprocess.run(["pg_dump", "--dbname", OWNER_URL, "-f", str(dump)],
                          capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip(f"pg_dump unavailable: {made.stderr[:160]}")

    suffix = uuid.uuid4().hex[:8]
    role, name = f"zolts_app_{suffix}", f"zolts_restore_{suffix}"
    # The dump grants to a role that does not exist on this cluster, which is
    # what a dump restored into a fresh managed project looks like.
    dump.write_text(dump.read_text().replace("zolts_app", role))

    admin = _with_database(OWNER_URL, "postgres")
    for target in (admin, OWNER_URL):
        created = subprocess.run(["psql", target, "-v", "ON_ERROR_STOP=1", "-q",
                                  "-c", f"create database {name}"],
                                 capture_output=True, text=True)
        if created.returncode == 0:
            break
    else:
        pytest.skip(f"cannot create a database to restore into: {created.stderr[:160]}")

    url = _with_database(OWNER_URL, name)
    environment = dict(os.environ, PYTHONPATH=".", ZOLTS_ENV="development",
                       APP_ROLE=role,
                       ZOLTS_SECRET_KEY=os.environ.get("ZOLTS_SECRET_KEY",
                                                       "restore-test-key"))
    result = subprocess.run(["bash", str(RESTORE), str(dump), url, PASSWORD],
                            capture_output=True, text=True, env=environment,
                            timeout=300)
    try:
        yield url, role, result
    finally:
        subprocess.run(["psql", target, "-q", "-c",
                        f"drop database if exists {name} with (force)"],
                       capture_output=True, text=True)
        subprocess.run(["psql", target, "-q", "-c", f"drop role if exists {role}"],
                       capture_output=True, text=True)


@requires_db
def test_the_restore_runs_all_four_steps_and_succeeds(restored):
    url, role, result = restored
    assert result.returncode == 0, (
        f"restore.sh failed:\nstdout: {result.stdout[-2000:]}\n"
        f"stderr: {result.stderr[-2000:]}")


@requires_db
def test_the_restore_creates_the_role_the_dump_only_grants_to(restored):
    """The silent failure this script exists for.

    Without step 1 every `GRANT ... TO <role>` in the dump fails, and `psql`
    without `ON_ERROR_STOP` exits 0 regardless: the restore reports success and
    the application role does not exist. The API then starts, connects as the
    owner, and cannot serve a single tenant.
    """
    import psycopg

    url, role, result = restored
    assert result.returncode == 0, result.stderr[-1500:]
    with psycopg.connect(url) as connection, connection.cursor() as cur:
        cur.execute("select count(*) from pg_roles where rolname = %s", (role,))
        assert cur.fetchone()[0] == 1, (
            f"the restored database has no {role}: every grant in the dump "
            "failed and the restore said nothing")


@requires_db
def test_the_restored_database_still_forces_row_level_security(restored):
    """A restore that carries the policies and loses the enforcement answers
    every query successfully while returning every tenant's rows to everyone."""
    import psycopg

    url, _role, result = restored
    assert result.returncode == 0, result.stderr[-1500:]
    with psycopg.connect(url) as connection, connection.cursor() as cur:
        cur.execute(
            "select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace"
            " where n.nspname = 'public' and c.relkind = 'r' and c.relforcerowsecurity")
        forced = cur.fetchone()[0]
        assert forced >= 25, f"only {forced} restored tables force row level security"


@requires_db
def test_the_restored_role_can_connect_and_still_cannot_read_without_a_tenant(restored):
    """Both halves. A role that cannot connect is a dead deployment; one that
    reads without declaring a tenant is an open one."""
    import psycopg

    url, role, result = restored
    assert result.returncode == 0, result.stderr[-1500:]
    with psycopg.connect(_as_role(url, role, PASSWORD)) as connection:
        with connection.cursor() as cur, pytest.raises(psycopg.errors.Error):
            cur.execute("select count(*) from account")


@requires_db
def test_the_restored_schema_is_current(restored):
    """The restored database has nothing left to apply.

    This is a property of the result, not a test of step 3 on its own: the
    dump is taken from a fully migrated database, so step 3 has nothing to do
    and deleting it changes nothing here. `test_a_dump_missing_its_last_migration_is_brought_forward`
    below is the one that needs step 3 to exist.
    """
    url, _role, result = restored
    assert result.returncode == 0, result.stderr[-1500:]
    check = subprocess.run(
        ["python3", "-m", "runtime.cli", "migrate"],
        capture_output=True, text=True,
        env=dict(os.environ, PYTHONPATH=".", ZOLTS_DATABASE_URL=url))
    assert check.returncode == 0, check.stderr[-1000:]
    # `migrate` reports what it applied. On a restored database that step 3
    # already brought forward there is nothing left to apply; anything here is
    # a migration the restore did not run.
    pending = json.loads(check.stdout)["applied"]
    assert pending == [], (
        f"the restored schema still had migrations pending: {pending}")


@requires_db
def test_a_restore_that_hits_an_error_fails_instead_of_reporting_success(db, tmp_path):
    """`ON_ERROR_STOP` is the difference between a restore and the appearance
    of one.

    `psql` without it runs every statement it can, skips the ones that fail,
    and exits 0. The operator sees "restored" and has a database missing
    whatever did not apply. So a dump with a statement that cannot succeed must
    make the script fail, loudly.
    """
    from tests.conftest import OWNER_URL

    if not RESTORE.exists():
        pytest.skip("scripts/restore.sh is absent")

    dump = tmp_path / "dump.sql"
    made = subprocess.run(["pg_dump", "--dbname", OWNER_URL, "-f", str(dump)],
                          capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip(f"pg_dump unavailable: {made.stderr[:160]}")

    suffix = uuid.uuid4().hex[:8]
    role, name = f"zolts_app_{suffix}", f"zolts_broken_{suffix}"
    text = dump.read_text().replace("zolts_app", role)
    dump.write_text(text + "\nselect * from a_table_that_does_not_exist;\n")

    admin = _with_database(OWNER_URL, "postgres")
    for target in (admin, OWNER_URL):
        created = subprocess.run(["psql", target, "-v", "ON_ERROR_STOP=1", "-q",
                                  "-c", f"create database {name}"],
                                 capture_output=True, text=True)
        if created.returncode == 0:
            break
    else:
        pytest.skip("cannot create a database to restore into")

    try:
        result = subprocess.run(
            ["bash", str(RESTORE), str(dump),
             _with_database(OWNER_URL, name), PASSWORD],
            capture_output=True, text=True, timeout=300,
            env=dict(os.environ, PYTHONPATH=".", ZOLTS_ENV="development",
                     APP_ROLE=role,
                     ZOLTS_SECRET_KEY=os.environ.get("ZOLTS_SECRET_KEY",
                                                     "restore-test-key")))
        assert result.returncode != 0, (
            "the restore reported success on a dump it could not fully apply")
    finally:
        subprocess.run(["psql", target, "-q", "-c",
                        f"drop database if exists {name} with (force)"],
                       capture_output=True, text=True)
        subprocess.run(["psql", target, "-q", "-c", f"drop role if exists {role}"],
                       capture_output=True, text=True)


@requires_db
def test_a_dump_from_an_older_schema_is_brought_forward(db, tmp_path):
    """Step 3, isolated, against a database that is genuinely behind.

    A dump is a snapshot of a schema at a moment. Code deployed after it
    expects the migrations that came later, so the restore applies them; a
    restore that stops at step 2 leaves a database the running runtime thinks
    is further along, and the failure surfaces as a missing column on the
    first request rather than at restore time.

    The first version of this test faked the condition by deleting the newest
    migration's bookkeeping row from a current dump. That state cannot occur:
    `Database.migrate` commits the DDL and the row together and rolls both back
    on failure, so a migration is either applied and recorded or neither. All
    that test proved was that re-running an `alter table ... add constraint` on
    a schema that already has it fails, which is true and is not a defect.

    So this builds a real predecessor: a database with every migration except
    the newest, dumped from there.
    """
    import psycopg

    from tests.conftest import OWNER_URL

    if not RESTORE.exists():
        pytest.skip("scripts/restore.sh is absent")

    migrations = sorted(Path("runtime/migrations").glob("*.sql"))
    if len(migrations) < 2:
        pytest.skip("only one migration; there is no older schema to restore")
    newest = migrations[-1].stem

    suffix = uuid.uuid4().hex[:8]
    role = f"zolts_app_{suffix}"
    old_name, restored_name = f"zolts_old_{suffix}", f"zolts_fwd_{suffix}"
    admin = _with_database(OWNER_URL, "postgres")

    def _create(name):
        for target in (admin, OWNER_URL):
            made = subprocess.run(["psql", target, "-v", "ON_ERROR_STOP=1", "-q",
                                   "-c", f"create database {name}"],
                                  capture_output=True, text=True)
            if made.returncode == 0:
                return target
        return None

    target = _create(old_name)
    if target is None:
        pytest.skip("cannot create a database to build an older schema in")
    if _create(restored_name) is None:
        subprocess.run(["psql", target, "-q", "-c", f"drop database {old_name}"],
                       capture_output=True, text=True)
        pytest.skip("cannot create a database to restore into")

    old_url = _with_database(OWNER_URL, old_name)
    restored_url = _with_database(OWNER_URL, restored_name)
    dump = tmp_path / "old.sql"
    try:
        # Every migration but the last, recorded exactly as `migrate` records
        # them, so the result is a database that is simply one version behind.
        with psycopg.connect(old_url) as connection:
            connection.execute(
                "create table if not exists schema_migration ("
                " version text primary key,"
                " applied_at timestamptz not null default now())")
            connection.commit()
            for path in migrations[:-1]:
                connection.execute(path.read_text())
                connection.execute("insert into schema_migration (version) values (%s)",
                                   (path.stem,))
                connection.commit()
            row = connection.execute(
                "select count(*) from schema_migration where version = %s",
                (newest,)).fetchone()
            assert row[0] == 0, "the predecessor already carries the newest migration"

        made = subprocess.run(["pg_dump", "--dbname", old_url, "-f", str(dump)],
                              capture_output=True, text=True)
        if made.returncode != 0:
            pytest.skip(f"pg_dump unavailable: {made.stderr[:160]}")
        dump.write_text(dump.read_text().replace("zolts_app", role))

        result = subprocess.run(
            ["bash", str(RESTORE), str(dump), restored_url, PASSWORD],
            capture_output=True, text=True, timeout=300,
            env=dict(os.environ, PYTHONPATH=".", ZOLTS_ENV="development",
                     APP_ROLE=role,
                     ZOLTS_SECRET_KEY=os.environ.get("ZOLTS_SECRET_KEY",
                                                     "restore-test-key")))
        assert result.returncode == 0, (
            f"restoring an older dump failed:\n{result.stdout[-1500:]}\n"
            f"{result.stderr[-1500:]}")
        with psycopg.connect(restored_url) as connection:
            found = connection.execute(
                "select count(*) from schema_migration where version = %s",
                (newest,)).fetchone()[0]
        assert found == 1, (
            f"the restore left {newest} unapplied; the running code expects it")
    finally:
        for name in (old_name, restored_name):
            subprocess.run(["psql", target, "-q", "-c",
                            f"drop database if exists {name} with (force)"],
                           capture_output=True, text=True)
        subprocess.run(["psql", target, "-q", "-c", f"drop role if exists {role}"],
                       capture_output=True, text=True)
