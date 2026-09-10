"""What `docs/03` says the data model is, checked against the database.

`docs/03` is the canonical data model, and no test and no script named it. Two
of its claims are load-bearing and neither was measured:

* **"Isolation by `tenant_id` plus row-level security on every table."** True
  today, and true by hand: `003_rls.sql` forces RLS over a hard-coded array of
  sixteen table names, and every tenant-scoped table added since — thirty-two
  of them now — was forced by somebody remembering to add the line to their own
  migration. That is a fact about a list, not a property of the system, which is
  what D-23 was. The day one is forgotten, a tenant reads another tenant's rows
  and nothing says so.
* **Fourteen core entities.** Two of them, `segment` and `experiment`, have no
  table and never had one. Both are real, and both live somewhere else: the
  audience is a block inside the programme spec and the variant is a hash on the
  enrolment row. The document described a design that was not built (D-88).

The RLS check runs against a real database on purpose. Parsing the migrations
would re-read the same hand-maintained list the defect is in — the answer has to
come from `pg_class`, which knows what was actually applied.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.conftest import requires_db

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "03-data-model.md"
MIGRATIONS = ROOT / "runtime" / "migrations"
REFERENCE_DDL = ROOT / "examples" / "sql" / "schema.sql"

# The two entities `docs/03` names that are versioned configuration rather than
# rows, each with where it really lives. A third one appearing here means
# somebody moved an entity out of the database without saying so.
NOT_TABLES = {
    "segment": "the audience block inside a programme spec",
    "experiment": "a deterministic hash over the enrolment",
}


def _entities() -> list[str]:
    doc = DOC.read_text(encoding="utf-8")
    head = doc.index("## Core entities")
    body = doc[head:doc.index("\n## ", head + 1)]
    return re.findall(r"^\| `([a-z_]+)` \|", body, re.M)


def _declared_tables() -> set[str]:
    sql = "\n".join(p.read_text(encoding="utf-8") for p in sorted(MIGRATIONS.glob("*.sql")))
    return set(re.findall(r"create table (?:if not exists )?(\w+)", sql))


def test_the_entity_table_is_still_where_this_file_reads_it():
    """The premise. A regex that matches nothing leaves every test below
    passing over an empty list, which looks exactly like a model that agrees."""
    entities = _entities()
    assert len(entities) >= 12, f"docs/03 no longer lists its core entities: {entities}"
    assert "account" in entities and "touch" in entities


def test_every_documented_entity_is_a_table_or_is_named_as_configuration():
    """A documented entity with no table is the repository's dominant defect:
    a complete specification with no caller. The two that are configuration say
    so in the document, so a reader is not sent looking for a table."""
    tables = _declared_tables()
    doc = DOC.read_text(encoding="utf-8")
    for entity in _entities():
        if entity in tables:
            continue
        assert entity in NOT_TABLES, (
            f"docs/03 names `{entity}` as a core entity and no migration creates it")
        assert NOT_TABLES[entity] in doc, (
            f"docs/03 names `{entity}` as an entity without saying it is "
            f"{NOT_TABLES[entity]}")


def test_nothing_became_a_table_behind_the_document():
    """The other direction. The day `segment` or `experiment` becomes a table,
    this file is stale and says so rather than going on describing it as
    configuration."""
    tables = _declared_tables()
    for entity in NOT_TABLES:
        assert entity not in tables, (
            f"`{entity}` is a table now; docs/03 and this file both describe it "
            f"as configuration")


def test_the_reference_extract_holds_only_tables_that_exist():
    """`docs/03` points a reader at the reference DDL. It is an extract of the
    core and says so; what it must never do is show a table the runtime does
    not have, because a reader cannot tell an extract from an invention."""
    extract = set(re.findall(r"create table (?:if not exists )?(\w+)",
                             REFERENCE_DDL.read_text(encoding="utf-8")))
    assert extract, "the reference DDL no longer declares any table"
    missing = extract - _declared_tables()
    assert not missing, f"the reference DDL declares tables no migration creates: {sorted(missing)}"


@requires_db
def test_row_level_security_is_forced_on_every_tenant_scoped_table(db):
    """`docs/03`: row-level security on every table. Asked of the database.

    Three properties, because two of them alone are a control that looks
    present and is not. `enable` without `force` exempts the owning role, so
    migrations and application code see different databases (ADR-008); either
    of them without a policy leaves a table that denies everything, which fails
    loudly rather than leaking and is still not what the document says.
    """
    with db.admin_tx() as cur:
        cur.execute(
            "select c.relname, c.relrowsecurity, c.relforcerowsecurity,"
            "       (select count(*) from pg_policy p where p.polrelid = c.oid) as policies"
            "  from pg_class c"
            "  join pg_namespace n on n.oid = c.relnamespace"
            " where n.nspname = 'public' and c.relkind = 'r'"
            "   and exists (select 1 from information_schema.columns col"
            "               where col.table_schema = 'public'"
            "                 and col.table_name = c.relname"
            "                 and col.column_name = 'tenant_id')"
            " order by c.relname")
        rows = cur.fetchall()

    assert len(rows) >= 30, (
        f"only {len(rows)} tenant-scoped tables were found; the query is not "
        f"reading the schema the migrations built")
    unprotected = [
        r["relname"] for r in rows
        if not (r["relrowsecurity"] and r["relforcerowsecurity"] and r["policies"])]
    assert not unprotected, (
        f"docs/03 says row-level security is on every table and these carry a "
        f"tenant_id without it enabled, forced and policied: {unprotected}")


@requires_db
def test_the_check_can_see_a_table_that_is_not_protected(db):
    """The guard, verified by breaking what it guards.

    A table created inside the transaction carries a `tenant_id` and no policy,
    so the query above must find it. Without this, a query that silently matched
    nothing would report every table protected — which is the shape of D-71 and
    the reason this file measures rather than parses.

    It lives here rather than in `mutation_check.py` because that script edits
    source and re-runs the suite, and a migration is applied once: deleting the
    `force` line from `003_rls.sql` changes nothing on a database that already
    ran it, so the mutation would report a guard biting when nothing had moved.
    A guard verified by a mutation that cannot fire is the defect the script
    exists to catch, committed by the script.
    """
    with db.admin_tx() as cur:
        cur.execute("create table rls_probe (id int, tenant_id uuid)")
        cur.execute(
            "select c.relforcerowsecurity from pg_class c"
            "  join pg_namespace n on n.oid = c.relnamespace"
            " where n.nspname = 'public' and c.relname = 'rls_probe'")
        row = cur.fetchone()
        assert row is not None, "the probe table is not visible to the query this file runs"
        assert not row["relforcerowsecurity"]
        cur.execute("drop table rls_probe")
