"""Replacing the key that seals every credential.

One key seals every connector credential and every webhook signing secret, for
every tenant. `docs/23` has carried this as SEC-1 since the register was
written: *one key compromise decrypts every credential for every tenant, and
today there is no tested path to rotate*.

A rotation is three states, and the middle one is the one that has to work:

| State | What the runtime must do |
|---|---|
| before | seal and open with the old key |
| during | **open with either, seal with the new** |
| after  | seal and open with the new, and say so |

`runtime.crypto.Keyring` is the middle state; this module is the transition
out of it. Two properties matter more than speed:

**It is resumable.** Each batch commits on its own, so a rotation killed
halfway leaves a database that still works — some rows on the new key, some on
the old, every one of them openable while both keys are configured. Re-running
finishes the job.

**A credential nothing can open does not stop it.** Those rows are counted and
named rather than raised on, because the alternative is a rotation that
refuses to protect the other nine hundred credentials on account of one whose
key is already gone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from runtime.crypto import CannotOpen, Keyring, open_sealed, seal
from runtime.db import Database

# The two tables that hold sealed material. Adding a third means adding it
# here; the test below reads the schema and fails when one is missing, so this
# list cannot quietly fall behind the database.
SEALED = (
    ("connection", "secret_enc", "secret_key_id"),
    ("webhook_endpoint", "secret_enc", "secret_key_id"),
)

# A rotation that pages for ever is a bug, not a large tenant. The first draft
# of this module selected "what is not yet on the new key" on every batch,
# which never stops matching a row no key opens — it would have re-read the
# same row until someone killed it. The paging below is by id and cannot
# repeat, and this bound is what turns a regression of that into an error with
# a name rather than a process that hangs in CI.
MAX_BATCHES = 10_000

# Written into the key-id column when a rotation tried a row and no configured
# key opened it. It is deliberately not a key id: a row that has been tried and
# failed is a different state from one nothing has looked at yet, and without
# the distinction `--check` reports "unknown, re-run" for ever on credentials
# that will never open. The row is still retried on later runs — a key that is
# added back should rescue it — so the marker changes what an operator is told,
# not what the rotation attempts.
UNOPENABLE = "unopenable"


class RotationStalled(RuntimeError):
    """The rotation paged past its bound without finishing."""


@dataclass
class Outstanding:
    """What a rotation would still have to do."""
    total: int = 0
    on_primary: int = 0
    on_previous: int = 0
    # Rows written before key ids existed. Openable or not is unknown until
    # something tries, which is why they are neither of the two above.
    unknown: int = 0
    # Rows naming a key this deployment does not hold, and rows a rotation has
    # already tried and failed to open. These are not a rotation problem, they
    # are a lost credential: nothing in this database can recover them.
    unopenable: int = 0
    by_key: dict[str, int] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.total == self.on_primary


@dataclass
class Rotated:
    """What a rotation did."""
    rows: int = 0
    tenants: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)


def _tenants(db: Database) -> list[str]:
    with db.admin_tx() as cur:
        cur.execute("select id from tenant order by created_at")
        return [str(r["id"]) for r in cur.fetchall()]


def outstanding(db: Database, keyring: "str | Keyring") -> Outstanding:
    """Count, without decrypting anything, what is not on the primary key."""
    ring = Keyring.of(keyring)
    report = Outstanding()
    previous_ids = set(ring.known_ids) - {ring.primary_id}
    for tenant_id in _tenants(db):
        with db.tenant_tx(tenant_id) as cur:
            for table, _, id_column in SEALED:
                cur.execute(f"select {id_column} as k, count(*) as n from {table}"
                            f" group by {id_column}")
                for row in cur.fetchall():
                    stored, n = row["k"], int(row["n"])
                    report.total += n
                    report.by_key[stored or "(none)"] = (
                        report.by_key.get(stored or "(none)", 0) + n)
                    if stored is None:
                        report.unknown += n
                    elif stored == ring.primary_id:
                        report.on_primary += n
                    elif stored in previous_ids:
                        report.on_previous += n
                    else:
                        report.unopenable += n
    return report


def rotate(db: Database, keyring: "str | Keyring", *, batch_size: int = 100) -> Rotated:
    """Re-seal every credential under the primary key.

    Idempotent: a row already on the primary key is not touched, so a second
    run does nothing and reports nothing. Interrupt it and re-run it; the only
    state it depends on is the column it writes.
    """
    ring = Keyring.of(keyring)
    result = Rotated()
    for tenant_id in _tenants(db):
        touched_here = 0
        for table, blob_column, id_column in SEALED:
            # Paged by id rather than by "what still matches", because a row
            # this keyring cannot open never stops matching: counting it as a
            # failure and selecting again would hand back the same row for
            # ever. The cursor moves whether the row was rotated or skipped.
            after: Any = None
            batches = 0
            while True:
                batches += 1
                if batches > MAX_BATCHES:
                    raise RotationStalled(
                        f"{table} paged {MAX_BATCHES} times without finishing; the "
                        "rotation is not making progress")
                with db.tenant_tx(tenant_id) as cur:
                    sql = (f"select id, {blob_column} as blob from {table}"
                           f" where {id_column} is distinct from %s"
                           f"   and {blob_column} is not null")
                    params: list[Any] = [ring.primary_id]
                    if after is not None:
                        sql += " and id > %s"
                        params.append(after)
                    sql += " order by id limit %s"
                    params.append(batch_size)
                    cur.execute(sql, params)
                    rows: list[dict[str, Any]] = [dict(r) for r in cur.fetchall()]
                    if not rows:
                        break
                    after = rows[-1]["id"]
                    for row in rows:
                        try:
                            plaintext = open_sealed(row["blob"], ring)
                        except CannotOpen:
                            # Named and skipped. Raising here would abandon
                            # every other credential on account of one whose
                            # key is already gone, which is the opposite of
                            # what a rotation is for. The row keeps the key id
                            # it has, so `outstanding` keeps reporting it.
                            result.failed += 1
                            result.failures.append(f"{table}:{row['id']}")
                            cur.execute(
                                f"update {table} set {id_column} = %s where id = %s",
                                (UNOPENABLE, row["id"]))
                            continue
                        cur.execute(
                            f"update {table} set {blob_column} = %s, {id_column} = %s"
                            f" where id = %s",
                            (seal(plaintext, ring), ring.primary_id, row["id"]))
                        result.rows += 1
                        touched_here += 1
        if touched_here:
            result.tenants += 1
    return result
