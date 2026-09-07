"""Replacing the key that seals every credential.

`docs/23` carried this as SEC-1 from the day the register was written: one key
seals every connector credential and every webhook secret for every tenant, and
there was no tested path to replace it. A key that leaks and cannot be replaced
is a permanent compromise of every customer's CRM — and the cheapest moment to
build the replacement is before there is any customer data to replace under.

The tests that matter are not the ones proving a rotated credential opens.
They are the three that make a rotation survivable: a half-finished one still
serves traffic, a re-run finishes it, and a credential nothing can open is
named rather than silently skipped or endlessly retried.
"""

from __future__ import annotations

import uuid

import pytest

from runtime import rotation
from runtime.crypto import CannotOpen, Keyring, key_id, open_sealed, seal
from runtime.provision import create_webhook_endpoint, store_connection
from tests.conftest import requires_db

OLD = "old-key-9f2c1b7a4e"
NEW = "new-key-31d8ac05fe"


def _both() -> Keyring:
    """The middle state of a rotation: seal with the new, open with either."""
    return Keyring(primary=NEW, previous=(OLD,))


def _connections(db, tenant_id: str) -> list[dict]:
    with db.tenant_tx(tenant_id) as cur:
        cur.execute("select id, provider, secret_enc, secret_key_id from connection"
                    " order by provider")
        return [dict(r) for r in cur.fetchall()]


def _seed(db, tenant_id: str, key, *, providers=("hubspot", "smartlead")) -> None:
    for n, provider in enumerate(providers):
        store_connection(db, tenant_id, provider=provider, secret=f"token-{n}",
                         secret_key=key)
    create_webhook_endpoint(db, tenant_id, provider="smartlead", secret_key=key,
                            secret="signing-secret")


# -- the primitive --------------------------------------------------------

def test_a_key_id_names_a_key_without_disclosing_it():
    assert key_id(OLD) != key_id(NEW)
    assert key_id(OLD) == key_id(OLD), "the id must be stable across processes"
    assert OLD not in key_id(OLD) and len(key_id(OLD)) == 12


def test_a_keyring_opens_what_a_previous_key_sealed():
    blob = seal("token", OLD)
    assert open_sealed(blob, _both()) == "token"


def test_the_wrong_key_fails_rather_than_returning_plausible_bytes():
    """AES-GCM authenticates, which is what makes trying keys in turn safe
    rather than sloppy: a miss is a tag failure, never a wrong answer."""
    with pytest.raises(CannotOpen, match="no configured key"):
        open_sealed(seal("token", OLD), NEW)


def test_the_refusal_says_what_to_do_about_it():
    try:
        open_sealed(seal("token", OLD), NEW)
    except CannotOpen as exc:
        assert "re-entered" in str(exc), (
            "a credential nothing can open is unrecoverable, and the message has "
            "to say so rather than implying a retry would help")


# -- the rotation ---------------------------------------------------------

@requires_db
def test_rotation_re_seals_every_credential_under_the_new_key(db, tenant):
    tid = str(tenant["id"])
    _seed(db, tid, OLD)

    before = rotation.outstanding(db, _both())
    assert before.total == 3 and before.on_previous == 3 and not before.complete

    done = rotation.rotate(db, _both())
    assert done.rows == 3 and done.failed == 0 and done.tenants == 1

    after = rotation.outstanding(db, _both())
    assert after.complete and after.on_primary == 3
    for row in _connections(db, tid):
        assert row["secret_key_id"] == key_id(NEW)
        # The point of the whole exercise: the old key is no longer needed.
        assert open_sealed(row["secret_enc"], NEW).startswith("token-")


@requires_db
def test_the_credential_still_opens_while_the_rotation_is_half_done(db, tenant):
    """The state a rotation actually spends its time in.

    A deployment that could not serve traffic mid-rotation would make rotating
    an outage, and a control nobody runs is not a control.
    """
    tid = str(tenant["id"])
    _seed(db, tid, OLD)
    rotation.rotate(db, _both(), batch_size=1)   # rewrites one row per batch

    with db.tenant_tx(tid) as cur:
        cur.execute("select secret_enc, secret_key_id from connection")
        rows = [dict(r) for r in cur.fetchall()]
    assert rows, "the fixture wrote nothing"
    for row in rows:
        assert open_sealed(row["secret_enc"], _both()), (
            "every row must open under the ring, whichever key sealed it")


@requires_db
def test_a_rotation_is_idempotent(db, tenant):
    tid = str(tenant["id"])
    _seed(db, tid, OLD)
    rotation.rotate(db, _both())
    again = rotation.rotate(db, _both())
    assert again.rows == 0 and again.tenants == 0, (
        "a second run must touch nothing; an operator will run it twice")


@requires_db
def test_a_rotation_is_resumable(db, tenant):
    """Each batch commits on its own, so a run killed halfway leaves a working
    database and a re-run finishes it."""
    tid = str(tenant["id"])
    _seed(db, tid, OLD, providers=("hubspot", "pipedrive", "salesforce", "smartlead"))

    # Interrupted after one row: rotate a single connection by hand, the way
    # the first batch of a killed run would have.
    with db.tenant_tx(tid) as cur:
        cur.execute("select id, secret_enc from connection order by id limit 1")
        row = dict(cur.fetchone())
        cur.execute("update connection set secret_enc = %s, secret_key_id = %s"
                    " where id = %s",
                    (seal(open_sealed(row["secret_enc"], OLD), NEW), key_id(NEW),
                     row["id"]))

    mid = rotation.outstanding(db, _both())
    assert mid.on_primary == 1 and mid.on_previous == 4

    finished = rotation.rotate(db, _both())
    assert finished.rows == 4, "the resumed run must skip what is already rotated"
    assert rotation.outstanding(db, _both()).complete


@requires_db
def test_a_credential_no_key_opens_is_named_and_does_not_stop_the_rotation(db, tenant):
    """The row whose key is already gone.

    Raising on it would abandon every other credential in the database on
    account of one that is unrecoverable anyway. Retrying it for ever — which
    is what selecting on "not yet rotated" would do — would be worse.
    """
    tid = str(tenant["id"])
    _seed(db, tid, OLD)
    with db.tenant_tx(tid) as cur:
        cur.execute("update connection set secret_enc = %s, secret_key_id = %s"
                    " where provider = 'hubspot'",
                    (seal("lost", "a-third-key-nobody-configured"), "deadbeefcafe"))

    done = rotation.rotate(db, _both())
    assert done.failed == 1 and done.rows == 2
    assert any("connection:" in f for f in done.failures)

    after = rotation.outstanding(db, _both())
    assert after.unopenable == 1 and not after.complete


@requires_db
def test_a_rotation_that_stops_making_progress_raises_rather_than_hanging(db, tenant,
                                                                          monkeypatch):
    """The bound that keeps a regression out of CI as an error rather than a hang.

    The first draft paged by "what is not yet rotated", which never stops
    matching a row no key opens. Verified by putting that draft back: the run
    had to be killed at 45 seconds. A test that hangs teaches nothing.
    """
    _seed(db, str(tenant["id"]), OLD)
    monkeypatch.setattr(rotation, "MAX_BATCHES", 1)
    with pytest.raises(rotation.RotationStalled, match="not making progress"):
        rotation.rotate(db, _both(), batch_size=1)


@requires_db
def test_a_credential_that_failed_to_open_stops_being_reported_as_unknown(db, tenant):
    """Found by running the command against a database with real junk in it.

    Rows written before key ids existed carry a null id, which `--check`
    reports as *unknown* — the honest answer until something tries. After a
    rotation has tried and failed, the answer is known and it is *unopenable*,
    and reporting it as unknown tells an operator to re-run a command that
    cannot help. The synthetic test above never caught this because its
    unopenable row carried a key id; the thirty-eight rows in the smoke
    database carried nulls.
    """
    tid = str(tenant["id"])
    _seed(db, tid, OLD)
    with db.tenant_tx(tid) as cur:
        cur.execute("update connection set secret_enc = %s, secret_key_id = null"
                    " where provider = 'hubspot'", (seal("lost", "a-key-nobody-holds"),))

    assert rotation.outstanding(db, _both()).unknown == 1
    rotation.rotate(db, _both())
    after = rotation.outstanding(db, _both())
    assert after.unknown == 0 and after.unopenable == 1, (
        "a row that has been tried is no longer unknown")


@requires_db
def test_rotation_covers_every_table_that_holds_sealed_material(db, tenant):
    """`rotation.SEALED` is a hand-written list, and a third sealed column
    added without touching it would be silently left on the old key."""
    with db.admin_tx() as cur:
        cur.execute("select table_name, column_name from information_schema.columns"
                    " where table_schema = 'public' and column_name like '%_enc'")
        columns = {(r["table_name"], r["column_name"]) for r in cur.fetchall()}
    covered = {(table, blob) for table, blob, _ in rotation.SEALED}
    assert columns == covered, (
        f"sealed columns the rotation does not cover: {sorted(columns - covered)}")


@requires_db
def test_a_new_credential_records_the_key_that_sealed_it(db, tenant):
    """Without this the rotation cannot report progress, and 'is it finished'
    goes back to being a decryption pass over every row."""
    tid = str(tenant["id"])
    store_connection(db, tid, provider="hubspot", secret="token", secret_key=_both())
    assert _connections(db, tid)[0]["secret_key_id"] == key_id(NEW)


# -- what the deployment says about it ------------------------------------

@requires_db
def test_preflight_reports_a_credential_it_cannot_open(db, tenant):
    """A runtime that starts cleanly and then cannot open a credential fails at
    the worst moment: mid-send, per tenant, with no explanation."""
    from runtime.config import Settings
    from runtime import preflight

    tid = str(tenant["id"])
    _seed(db, tid, OLD)
    settings = Settings(database_url="postgres://x", app_database_url="postgres://y",
                        secret_key=NEW, previous_secret_keys=(), environment="production",
                        lease_seconds=60, worker_batch=25, dry_run=False,
                        agents_enabled=False)
    report = preflight.Report()
    preflight._sealed_credentials(report, settings, db, production=True)
    check = report.checks[-1]
    assert not check.ok and "does not hold" in check.detail
    assert check.fatal, "in production this is a refusal to start, not a note"


@requires_db
def test_preflight_is_content_once_the_rotation_is_finished(db, tenant):
    from runtime.config import Settings
    from runtime import preflight

    tid = str(tenant["id"])
    _seed(db, tid, OLD)
    rotation.rotate(db, _both())
    settings = Settings(database_url="postgres://x", app_database_url="postgres://y",
                        secret_key=NEW, previous_secret_keys=(), environment="production",
                        lease_seconds=60, worker_batch=25, dry_run=False,
                        agents_enabled=False)
    report = preflight.Report()
    preflight._sealed_credentials(report, settings, db, production=True)
    assert report.checks[-1].ok


@requires_db
def test_the_runtime_opens_a_previous_keys_credential_end_to_end(db, tenant, fake):
    """Not the rotation — the window. A worker holding the ring must be able to
    send with a credential the old key sealed, or rotating means downtime."""
    from runtime.engine.worker import Worker

    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="provider-token", secret_key=OLD)
    worker = Worker(db, secret_key=_both())
    with db.tenant_tx(tid) as cur:
        provider, credential, _ = worker._connection_for(cur, "email")
    assert (provider, credential) == ("fake", "provider-token")
