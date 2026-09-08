"""A policy decision can be produced years later, or it is not an audit trail.

`policy_decision` recorded a rule key, a jurisdiction and a reason — and the
rules themselves lived in `zolts/policy.py`, code that changes with every
release. So the one question the table exists to answer, *under which rule was
this person contacted*, resolved to whatever the pack says today (D-53). The
module's own docstring said packs were data rather than code while the only
pack was a dict in that file.

Two properties here: a pack is a document that can be published without a
deployment, and a decision names the exact document that produced it.
"""

from __future__ import annotations

import json
import uuid
from datetime import time

import pytest

from runtime import policy_packs
from tests.conftest import requires_db
from zolts import policy


# -- the document ----------------------------------------------------------

def test_the_shipped_pack_round_trips_through_its_document():
    document = policy.to_document(policy.PACK_V1)
    assert policy.from_document(document) == policy.PACK_V1
    assert policy.pack_digest(policy.from_document(document)) == \
        policy.pack_digest(policy.PACK_V1)


def test_the_digest_covers_every_field_a_rule_decides_by():
    """A document that omitted one would hash the same across a change to it,
    and the digest would certify a rule it never covered."""
    original = policy.pack_digest(policy.PACK_V1)
    import dataclasses

    for change in (
            {"required_basis": {"email": policy.Basis.CONSENT}},
            {"blocked_channels": frozenset({"email"})},
            {"suppression_lists": ("some_registry",)},
            {"quiet_hours": (time(22, 0), time(6, 0))}):
        moved = dict(policy.PACK_V1)
        moved["ES"] = dataclasses.replace(policy.PACK_V1["ES"], **change)
        assert policy.pack_digest(moved) != original, change


def test_a_pack_that_cannot_be_read_is_refused_rather_than_emptied():
    """A pack silently missing a country is a pack that allows cold email
    there, which is the failure the default-deny posture exists to prevent."""
    with pytest.raises(policy.PackDocumentError, match="no rules"):
        policy.from_document({})
    with pytest.raises(policy.PackDocumentError, match="no rules"):
        policy.from_document({"rules": {}})
    with pytest.raises(policy.PackDocumentError, match="DE"):
        policy.from_document({"rules": {"DE": {"country": "DE"}}})
    with pytest.raises(policy.PackDocumentError, match="ES"):
        policy.from_document({"rules": {"ES": {
            "country": "ES", "required_basis": {"email": "vibes"},
            "quiet_hours": ["20:00:00", "08:00:00"]}}})


# -- published, not deployed -------------------------------------------------

@requires_db
def test_a_fresh_database_has_a_pack_that_can_be_named(db):
    """Installed with the schema. A deployment with tables and no active pack
    is one where nothing may be decided at all."""
    with db.admin_tx() as cur:
        active = policy_packs.active(cur)
    assert active["digest"] == policy.pack_digest(policy.PACK_V1)
    assert active["version"] == policy.PACK_V1_VERSION
    assert policy_packs.rules_of(active) == policy.PACK_V1


@requires_db
def test_publishing_a_pack_needs_no_deployment_and_keeps_the_old_one(db):
    """The claim `zolts/policy.py` used to make while the only pack was a dict
    in it: a regulatory change ships without a release."""
    with db.admin_tx() as cur:
        before = policy_packs.active(cur)

        document = policy.to_document(policy.PACK_V1)
        document["rules"]["DE"]["blocked_channels"] = ["voice", "whatsapp"]
        published = policy_packs.publish(cur, document, version="2", published_by="operator")

        assert published["digest"] != before["digest"]
        assert policy_packs.active(cur)["digest"] == published["digest"]
        # The pack that decided yesterday is still readable today.
        kept = policy_packs.get(cur, before["digest"])
        assert kept is not None and kept["active"] is False
        assert policy_packs.rules_of(kept)["DE"].blocked_channels == frozenset({"voice"})
        assert "whatsapp" in policy_packs.rules_of(published)["DE"].blocked_channels

        assert len(policy_packs.history(cur)) >= 2


@requires_db
def test_only_one_pack_is_ever_active(db):
    """Two active packs is a runtime where the answer depends on which row was
    read first. The index refuses it; this asserts the index exists."""
    import psycopg

    with db.admin_tx() as cur:
        policy_packs.publish(cur, policy.to_document(policy.PACK_V1), version="2",
                             published_by="operator")
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute("insert into policy_pack (version, digest, body, active, published_by)"
                        " values ('3','other','{}'::jsonb, true, 'test')")


@requires_db
def test_a_pack_document_that_cannot_be_read_is_never_stored(db):
    with db.admin_tx() as cur:
        with pytest.raises(policy.PackDocumentError):
            policy_packs.publish(cur, {"rules": {"ES": {"country": "ES"}}},
                                 version="3", published_by="operator")
        cur.execute("select count(*) as n from policy_pack where version = '3'")
        assert cur.fetchone()["n"] == 0


# -- the decision names the rules that produced it --------------------------

@requires_db
def test_every_decision_the_gate_takes_names_its_pack(db, tenant, fake):
    from runtime.engine import gate
    from runtime.repo import entities

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        person = entities.upsert_person(
            cur, tid, email=f"p-{uuid.uuid4().hex[:8]}@example.com", full_name="P",
            country="ES", consent_state={"email": {"basis": "legitimate_interest",
                                                   "source": "test"}})
        result = gate.check(cur, tid, person=dict(person), channel="email",
                            enrollment_id=None, program_spec={})
        cur.execute("select pack_version, pack_digest from policy_decision where id = %s",
                    (result.decision_id,))
        row = cur.fetchone()

    with db.admin_tx() as cur:
        active = policy_packs.active(cur)
    assert row["pack_digest"] == active["digest"]
    assert row["pack_version"] == active["version"]


@requires_db
def test_a_decision_cannot_be_recorded_without_naming_a_pack(db, tenant):
    """Half of invariant 3. A reason that names a rule key is only half an
    answer while the rules live in code that changes every release."""
    from runtime.repo import ledger

    with db.tenant_tx(str(tenant["id"])) as cur:
        with pytest.raises(ledger.DecisionWithoutAReason, match="policy pack"):
            ledger.record_decision(
                cur, str(tenant["id"]), subject_type="person", subject_id=str(uuid.uuid4()),
                action="email.send", decision="allow", rule_key="eu.b2b",
                jurisdiction="ES", rationale="within the rule",
                pack_version="1", pack_digest="")


@requires_db
def test_the_gate_evaluates_the_published_pack_not_the_shipped_dict(db, tenant, fake):
    """The whole point of publishing one. A pack that blocks email in Spain
    has to deny a Spanish contact the shipped pack would allow."""
    from runtime.engine import gate
    from runtime.repo import entities

    tid = str(tenant["id"])
    document = policy.to_document(policy.PACK_V1)
    document["rules"]["ES"]["blocked_channels"] = ["email"]
    with db.admin_tx() as cur:
        policy_packs.publish(cur, document, version="2-blocks-es", published_by="operator")

    with db.tenant_tx(tid) as cur:
        person = entities.upsert_person(
            cur, tid, email=f"q-{uuid.uuid4().hex[:8]}@example.com", full_name="Q",
            country="ES", consent_state={"email": {"basis": "legitimate_interest",
                                                   "source": "test"}})
        result = gate.check(cur, tid, person=dict(person), channel="email",
                            enrollment_id=None, program_spec={})
    assert result.allowed is False
    assert result.decision == "deny"

    # And restore the shipped pack, so the rest of the suite decides under it.
    with db.admin_tx() as cur:
        policy_packs.publish(cur, policy.to_document(policy.PACK_V1),
                             version=policy.PACK_V1_VERSION, published_by="test")


@requires_db
def test_the_console_says_which_packs_decided(db, tenant, fake):
    from runtime.api import console
    from runtime.engine import gate
    from runtime.repo import entities

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        person = entities.upsert_person(
            cur, tid, email=f"r-{uuid.uuid4().hex[:8]}@example.com", full_name="R",
            country="ES", consent_state={"email": {"basis": "legitimate_interest",
                                                   "source": "test"}})
        gate.check(cur, tid, person=dict(person), channel="email", enrollment_id=None,
                   program_spec={})
        view = console.policy_view(cur)

    with db.admin_tx() as cur:
        digest = policy_packs.active(cur)["digest"]
    assert view["packs"] and view["packs"][0]["digest"] == digest
    assert view["decisionsWithNoPack"] == 0
    assert view["recent"][0]["pack"] == digest[:12]


@requires_db
def test_the_command_line_publishes_a_pack_and_prints_the_active_one(
        db, monkeypatch, capsys, tmp_path):
    from runtime import cli
    from tests.conftest import APP_URL, OWNER_URL, SECRET

    monkeypatch.setenv("ZOLTS_DATABASE_URL", OWNER_URL)
    monkeypatch.setenv("ZOLTS_APP_DATABASE_URL", APP_URL)
    monkeypatch.setenv("ZOLTS_SECRET_KEY", SECRET)

    assert cli.main(["policy-pack"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert len(shown["digest"]) == 64

    document = policy.to_document(policy.PACK_V1)
    document["rules"]["FR"]["suppression_lists"] = ["bloctel", "extra_registry"]
    path = tmp_path / "pack.json"
    path.write_text(json.dumps(document))

    assert cli.main(["policy-pack", "--file", str(path)]) == 2, "a version is required"
    assert "--version" in capsys.readouterr().err

    assert cli.main(["policy-pack", "--file", str(path), "--version", "2"]) == 0
    published = json.loads(capsys.readouterr().out)
    assert published["digest"] != shown["digest"]

    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"rules": {"ES": {"country": "ES"}}}))
    assert cli.main(["policy-pack", "--file", str(broken), "--version", "3"]) == 2
    assert "cannot be published" in capsys.readouterr().err

    # Put the shipped pack back for the rest of the suite.
    with db.admin_tx() as cur:
        policy_packs.publish(cur, policy.to_document(policy.PACK_V1),
                             version=policy.PACK_V1_VERSION, published_by="test")
