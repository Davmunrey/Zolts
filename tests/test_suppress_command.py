"""The operator's path to honouring an unsubscribe by hand.

`repo.entities.suppress` existed from the first migration, and every caller was
the runtime acting on a provider's event. So the one moment a person most needs
it — the inbound handler has broken, `docs/25` says apply the unsubscribes
first, and the automation that would have done it is the thing that is down —
had no command at all. A capability with no operator surface is the same defect
as a specification with no caller, seen from the other side.

The claim these assert is not that a row appears. It is that the send stops.
"""

from __future__ import annotations

import json as jsonlib
import uuid

import pytest

from runtime import cli
from tests.conftest import APP_URL, OWNER_URL, requires_db


@pytest.fixture(autouse=True)
def _cli_env(monkeypatch):
    """`cli.main` builds its own Database from the environment, as it does on a
    real host. Pointing it at the test cluster is what makes this an end-to-end
    exercise of the command rather than of the function beneath it."""
    monkeypatch.setenv("ZOLTS_DATABASE_URL", OWNER_URL or "")
    monkeypatch.setenv("ZOLTS_APP_DATABASE_URL", APP_URL or "")
    monkeypatch.setenv("ZOLTS_SECRET_KEY", "cli-test-secret-key")


def _suppress(tid: str, value: str, reason: str, capsys, scope: str = "email") -> dict:
    capsys.readouterr()
    code = cli.main(["suppress", "--tenant", tid, "--value", value,
                     "--scope", scope, "--reason", reason])
    assert code == 0
    return jsonlib.loads(capsys.readouterr().out)


def _person(db, tid: str, email: str) -> dict:
    from runtime.repo import entities

    with db.tenant_tx(tid) as cur:
        return dict(entities.upsert_person(
            cur, tid, email=email, full_name="P", country="ES",
            consent_state={"email": {"basis": "legitimate_interest", "source": "test"}}))


def _decision(db, tid: str, person: dict):
    from runtime.engine import gate

    with db.tenant_tx(tid) as cur:
        return gate.check(cur, tid, person=person, channel="email",
                          enrollment_id=None, program_spec={})


@requires_db
def test_an_operator_suppression_actually_stops_the_send(db, tenant, capsys):
    """The whole point. A row in a table nothing consults would be theatre."""
    tid = str(tenant["id"])
    email = f"stop-{uuid.uuid4().hex[:8]}@example.com"
    person = _person(db, tid, email)

    allowed = _decision(db, tid, person)
    assert allowed.allowed, (
        "this contact must be sendable before suppression, or the test proves nothing")

    _suppress(tid, email, "unsubscribed by reply", capsys)

    refused = _decision(db, tid, person)
    assert not refused.allowed
    assert "suppression" in refused.rule_key, refused.rule_key


@requires_db
def test_the_address_is_matched_however_it_was_capitalised(db, tenant, capsys):
    """An unsubscribe rarely arrives spelled the way the contact was stored.
    The column is `citext`; this asserts the runtime relies on that rather than
    on both sides happening to be lowercase."""
    tid = str(tenant["id"])
    email = f"Mixed-{uuid.uuid4().hex[:8]}@Example.com"
    person = _person(db, tid, email)

    _suppress(tid, email.upper(), "unsubscribed by phone", capsys)

    refused = _decision(db, tid, person)
    assert not refused.allowed, (
        "an unsubscribe was ignored because the provider capitalised it differently")


@requires_db
def test_running_it_twice_says_so_and_changes_nothing(db, tenant, capsys):
    """An operator repeats the command to confirm it took. That must read as
    confirmation, and it must not quietly rewrite the reason already on file —
    the stored reason is what an auditor reads."""
    tid = str(tenant["id"])
    email = f"twice-{uuid.uuid4().hex[:8]}@example.com"

    first = _suppress(tid, email, "unsubscribed by reply", capsys)
    assert first["note"] == "suppressed now"
    assert first["source"] == "operator"

    second = _suppress(tid, email, "some later wording", capsys)
    assert second["reason"] == "unsubscribed by reply", (
        "the second run overwrote the reason the first one recorded")
    assert second["since"] == first["since"]
    assert "already suppressed" in second["note"]


@requires_db
def test_a_domain_suppression_stops_everyone_behind_it(db, tenant, capsys):
    """The scope that matters after a complaint from a company rather than a
    person: one instruction, every address at that domain."""
    tid = str(tenant["id"])
    domain = f"d{uuid.uuid4().hex[:8]}.example"
    person = _person(db, tid, f"anyone@{domain}")

    assert _decision(db, tid, person).allowed

    _suppress(tid, domain, "the company asked, in writing", capsys, scope="domain")

    refused = _decision(db, tid, person)
    assert not refused.allowed, (
        "a domain suppression did not reach an address behind it")


@requires_db
def test_the_scope_is_constrained_to_what_the_column_accepts(db, tenant):
    """`suppression.scope` has a check constraint. A typo that reached the
    database would raise three frames down, at three in the morning."""
    tid = str(tenant["id"])
    with pytest.raises(SystemExit):
        cli.main(["suppress", "--tenant", tid, "--value", "x@example.com",
                  "--scope", "e-mail", "--reason", "typo"])
