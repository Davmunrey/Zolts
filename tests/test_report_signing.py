"""The report a CFO opens: a signed export, verified on a machine that has
never seen the database.

ADR-043 froze the report with a digest; a digest proves only that a document
is consistent with itself. The export now carries an Ed25519 signature over
the digest and the rendered prose, made with a key the instance publishes,
and `scripts/verify_report.py` establishes three things with `zolts` alone:
the figures rebuild to the digest they quote, the prose is the prose that
was signed, and the key that signed is the key that was pinned (`docs/28`,
OX-5). The exit criterion is the third: an exported report verifies with the
public key and nothing else.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from runtime import reportsig as keys
from tests.conftest import requires_db
from tests.test_report_schema_version import VERSION_ONE_BODY, VERSION_ONE_DIGEST, _today
from zolts import reportsig
from zolts.reportsig import (ALGORITHM, CHECKS, KIND, SEED_BYTES, Signer, SigningError,
                             document, key_id_of, verify)

ROOT = Path(__file__).resolve().parent.parent
SEED = bytes(range(SEED_BYTES))
OTHER = bytes(range(1, SEED_BYTES + 1))


def _export(signer: Signer = Signer.from_seed(SEED), **overrides) -> dict:
    report = _today()
    fields = dict(body=report.canonical(), rendered=report.render_markdown(),
                  program_key="flagship", program_version="2.1.0",
                  period_start="2026-05-01", period_end="2026-06-01",
                  verdict=report.verdict, frozen_at="2026-06-01T00:00:00+00:00",
                  frozen_by="operator", signer=signer, keys_url="https://z.example/keys")
    fields.update(overrides)
    return document(**fields)


# -- the rule ------------------------------------------------------------


def test_a_signed_export_verifies_under_the_pinned_key_and_names_every_check():
    signer = Signer.from_seed(SEED)
    doc = _export(signer)
    verdict = verify(doc, base64.b64encode(signer.public_key).decode())
    assert verdict.ok
    assert list(verdict.checks) == list(CHECKS)
    assert all(v == "ok" for v in verdict.checks.values())
    assert verdict.key_source == "pinned"
    assert verdict.digest == doc["digest"] == _today().digest()
    assert doc["kind"] == KIND and doc["signature"]["algorithm"] == ALGORITHM


def test_without_a_pinned_key_the_verdict_says_whose_key_it_used():
    """The document's own key proves it was not altered after signing, and
    nothing about who signed it. The verifier says so rather than passing
    the weaker check off as the stronger one."""
    verdict = verify(_export())
    assert verdict.ok and verdict.key_source == "the document's own"


def test_a_figure_changed_after_signing_fails_on_the_digest():
    doc = copy.deepcopy(_export())
    doc["report"]["primary"]["treatment_converted"] += 1
    verdict = verify(doc)
    assert not verdict.ok
    assert verdict.checks["figures"] == "ok"
    assert "the figures hash to" in verdict.checks["digest"]
    assert verdict.checks["signature"] == "not reached"


def test_a_digest_edited_to_match_changed_figures_still_fails_on_the_signature():
    """The attack a digest alone cannot stop: change a figure, recompute the
    digest, hand it over. The signature was made over the old digest."""
    doc = copy.deepcopy(_export())
    doc["report"]["primary"]["treatment_converted"] += 1
    doc["digest"] = reportsig.digest_of(doc["report"])
    verdict = verify(doc)
    assert verdict.checks["digest"] == "ok"
    assert not verdict.ok and "does not cover" in verdict.checks["signature"]


def test_prose_edited_after_signing_fails_even_though_the_figures_stand():
    doc = copy.deepcopy(_export())
    doc["rendered"] = doc["rendered"] + "\n\nThe lift was decisive.\n"
    assert doc["rendered"] != _export()["rendered"], "the premise: the prose changed"
    verdict = verify(doc)
    assert verdict.checks["digest"] == "ok"
    assert not verdict.ok and verdict.checks["signature"] != "ok"


def test_the_wrong_pinned_key_fails_on_the_key_check_not_the_signature():
    doc = _export()
    other = base64.b64encode(Signer.from_seed(OTHER).public_key).decode()
    verdict = verify(doc, other)
    assert not verdict.ok
    assert "the signature names" in verdict.checks["key"]
    assert verdict.checks["signature"] == "not reached"


def test_a_document_that_vouches_for_a_swapped_key_fails_on_its_own_terms():
    """Swap the carried key for another and re-sign nothing: the key id in
    the signature no longer names the key carried."""
    doc = copy.deepcopy(_export())
    doc["public_key"] = base64.b64encode(Signer.from_seed(OTHER).public_key).decode()
    assert not verify(doc).ok


def test_a_report_frozen_under_the_first_schema_version_still_signs_and_verifies():
    """D-72's property carried through: the rebuild takes the shape the body
    declares, so a document from before the form was versioned signs over
    the digest the partner's letter quotes."""
    signer = Signer.from_seed(SEED)
    doc = _export(signer, body=VERSION_ONE_BODY,
                  rendered=reportsig.from_mapping(VERSION_ONE_BODY).render_markdown())
    assert doc["digest"] == VERSION_ONE_DIGEST
    assert verify(doc, base64.b64encode(signer.public_key).decode()).ok


def test_the_wrong_kind_and_a_body_that_does_not_rebuild_are_named():
    assert "is not zolts.report.v1" in verify({"kind": "zolts.invoice.v1"}).checks["kind"]
    doc = copy.deepcopy(_export())
    del doc["report"]["primary"]
    assert "does not rebuild" in verify(doc).checks["figures"]


def test_a_seed_of_the_wrong_length_is_refused():
    with pytest.raises(SigningError):
        Signer.from_seed(b"short")


def test_the_key_id_names_the_public_key_and_nothing_secret():
    signer = Signer.from_seed(SEED)
    assert signer.key_id == key_id_of(signer.public_key)
    assert len(signer.key_id) == 12 and SEED.hex()[:12] != signer.key_id


# -- where the key comes from --------------------------------------------


def test_the_seed_derives_from_the_sealing_secret_and_differs_from_its_aes_key():
    from runtime.crypto import _key

    seed = keys.seed_from_secret("devdevdevdevdevdevdevdevdevdevdev")
    assert len(seed) == SEED_BYTES
    assert seed == keys.seed_from_secret("devdevdevdevdevdevdevdevdevdevdev"), "stable"
    assert seed != _key("devdevdevdevdevdevdevdevdevdevdev"), (
        "the signing seed and the sealing key are unrelated bytes of one secret")
    assert seed != keys.seed_from_secret("another secret")


def test_an_override_seed_wins_over_the_derivation(monkeypatch):
    monkeypatch.setenv(keys.OVERRIDE, base64.b64encode(SEED).decode())
    assert keys.signer_for("whatever").key_id == Signer.from_seed(SEED).key_id
    monkeypatch.setenv(keys.OVERRIDE, base64.b64encode(b"short").decode())
    with pytest.raises(SigningError):
        keys.signer_for("whatever")
    monkeypatch.delenv(keys.OVERRIDE)
    assert keys.signer_for("whatever").key_id != Signer.from_seed(SEED).key_id


def test_a_rotated_secret_still_publishes_the_key_it_used_to_sign_with(monkeypatch):
    from runtime.crypto import Keyring

    monkeypatch.delenv(keys.OVERRIDE, raising=False)
    old, new = "old-secret-old-secret-old-secret", "new-secret-new-secret-new-secret"
    signed_before = _export(keys.signer_for(old))
    published = keys.published_keys(Keyring(primary=new, previous=(old,)))
    assert published["keys"][0]["current"] is True
    assert published["keys"][0]["key_id"] == keys.signer_for(new).key_id
    older = [k for k in published["keys"] if not k["current"]]
    assert [k["key_id"] for k in older] == [signed_before["signature"]["key_id"]]
    assert verify(signed_before, older[0]["public_key"]).ok


# -- the verifier, with nothing but the document and the key ---------------


def test_the_script_verifies_with_the_public_key_and_no_database(tmp_path):
    signer = Signer.from_seed(SEED)
    doc = _export(signer)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(doc))
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("ZOLTS_")} | {"PYTHONPATH": str(ROOT)}
    run = subprocess.run(
        [sys.executable, "scripts/verify_report.py", str(path),
         "--public-key", base64.b64encode(signer.public_key).decode()],
        cwd=ROOT, capture_output=True, text=True, env=env)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "VERIFIED" in run.stdout and "pinned" in run.stdout
    assert doc["digest"] in run.stdout

    tampered = copy.deepcopy(doc)
    tampered["report"]["primary"]["treatment_converted"] += 1
    path.write_text(json.dumps(tampered))
    run = subprocess.run(
        [sys.executable, "scripts/verify_report.py", str(path),
         "--public-key", base64.b64encode(signer.public_key).decode()],
        cwd=ROOT, capture_output=True, text=True, env=env)
    assert run.returncode == 1 and "NOT VERIFIED" in run.stdout

    run = subprocess.run([sys.executable, "scripts/verify_report.py", str(tmp_path / "no.json")],
                         cwd=ROOT, capture_output=True, text=True, env=env)
    assert run.returncode == 2


def test_the_script_imports_nothing_from_the_runtime():
    """A machine that has never seen the database has no runtime configured
    and must not need one."""
    text = (ROOT / "scripts" / "verify_report.py").read_text()
    assert "from runtime" not in text and "import runtime" not in text
    assert "psycopg" not in text


# -- against a real database ---------------------------------------------


def _frozen(cur, tid: str) -> tuple[dict, dict]:
    from runtime import metering, reporting

    cur.execute(
        "insert into program (tenant_id, key, version, spec, spec_hash, status)"
        " values (%s,'signed','1.0.0',%s,'h','live') returning *",
        (tid, '{"experiment": {"holdout_pct": 10, "primary_metric": "m"}}'))
    program = dict(cur.fetchone())
    for variant, n in (("treatment", 3), ("control", 2)):
        for _ in range(n):
            cur.execute(
                "insert into enrollment (tenant_id, program_id, entity_type, entity_id,"
                " variant, state, entered_at) values (%s,%s,'account',gen_random_uuid(),"
                " %s,'running',now())", (tid, program["id"], variant))
    cur.execute("select * from tenant where id = %s", (tid,))
    tenant_row = dict(cur.fetchone())
    period = metering.open_period(cur, tenant_row)
    closed = metering.close_period(cur, tenant_row, str(period["id"]))
    row = reporting.freeze(cur, tid, program, closed, frozen_by="operator")
    return dict(row), program


@requires_db
def test_a_frozen_row_exports_as_a_document_the_script_verifies(db, tenant, tmp_path):
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        row, program = _frozen(cur, tid)
    doc = keys.export(row, program, "devdevdevdevdevdevdevdevdevdevdev",
                      keys_url="https://z.example/.well-known/zolts-signing-keys.json")
    assert doc["digest"] == row["digest"], "the export quotes the row's own digest"
    assert doc["rendered"] == row["rendered"]
    public = keys.published_keys("devdevdevdevdevdevdevdevdevdevdev")["keys"][0]["public_key"]
    assert verify(doc, public).ok
    path = tmp_path / "report.json"
    path.write_text(json.dumps(doc))
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("ZOLTS_")} | {"PYTHONPATH": str(ROOT)}
    run = subprocess.run(
        [sys.executable, "scripts/verify_report.py", str(path), "--public-key", public],
        cwd=ROOT, capture_output=True, text=True, env=env)
    assert run.returncode == 0, run.stdout + run.stderr


@requires_db
def test_the_export_and_the_keys_answer_over_http(db, tenant):
    from fastapi.testclient import TestClient

    from runtime.api.app import create_app
    from runtime.provision import issue_api_key

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        row, _ = _frozen(cur, tid)
    client = TestClient(create_app(db, secret_key="devdevdevdevdevdevdevdevdevdevdev"),
                        raise_server_exceptions=False)
    token = issue_api_key(db, tid, "test", []).token

    published = client.get(keys.KEYS_PATH)
    assert published.status_code == 200, published.text
    assert published.json()["keys"][0]["current"] is True

    exported = client.get(f"/v1/reports/{row['id']}/export", headers={"x-api-key": token})
    assert exported.status_code == 200, exported.text
    doc = exported.json()
    assert doc["kind"] == KIND
    assert doc["keys_url"].endswith(keys.KEYS_PATH)
    assert verify(doc, published.json()["keys"][0]["public_key"]).ok
    assert "attachment" in exported.headers.get("content-disposition", "")

    missing = client.get("/v1/reports/00000000-0000-0000-0000-000000000000/export",
                         headers={"x-api-key": token})
    assert missing.status_code == 404


@requires_db
def test_the_command_line_exports_signed_documents_the_script_verifies(db, tenant, tmp_path,
                                                                       monkeypatch):
    from runtime import cli
    from tests.conftest import APP_URL, OWNER_URL

    monkeypatch.setenv("ZOLTS_DATABASE_URL", OWNER_URL)
    monkeypatch.setenv("ZOLTS_APP_DATABASE_URL", APP_URL)
    monkeypatch.setenv("ZOLTS_SECRET_KEY", "devdevdevdevdevdevdevdevdevdevdev")
    monkeypatch.delenv(keys.OVERRIDE, raising=False)
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        row, _ = _frozen(cur, tid)
    out = tmp_path / "exports"
    assert cli.main(["report", "--tenant", tid, "--export", str(out)]) == 0
    written = list(out.glob("*.json"))
    assert len(written) == 1 and written[0].name.startswith("signed-")
    doc = json.loads(written[0].read_text())
    assert doc["digest"] == row["digest"]
    public = keys.published_keys("devdevdevdevdevdevdevdevdevdevdev")["keys"][0]["public_key"]
    assert verify(doc, public).ok


def test_the_console_offers_the_export_beside_every_frozen_report():
    surface = (ROOT / "design" / "console.html").read_text(encoding="utf-8")
    body = surface[surface.index("function frozenReports("):]
    body = body[:body.index("\n}")]
    assert "/export" in body, "no export link on the frozen report"
    assert "r.digest" in body and ".slice(0, 32)" not in body, (
        "the digest is shown truncated; the letter quotes all sixty-four characters")
