"""The baseline: the "before" that cannot be reconstructed afterwards.

`docs/17` calls capturing it the irreversible phase-1 requirement, `docs/13`
an exit criterion and `docs/14` a KPI, and nothing stored one (D-41). What
makes it worth a table rather than a form is the property tested most here:
**it is written once.** A baseline that can be corrected after the pilot has
run is a baseline that will be, and then the lift it exists to measure is a
comparison with a number somebody chose afterwards (ADR-042).
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from runtime.api.app import create_app
from runtime.provision import issue_api_key
from runtime.repo import baseline as baseline_repo
from tests.conftest import requires_db
from zolts.baseline import Baseline, BaselineError, from_mapping

DECLARED = {
    "window_start": "2026-06-01", "window_end": "2026-08-30",
    "spend_tools_micros": 1_200_000_000, "spend_data_micros": 800_000_000,
    "spend_sending_micros": 300_000_000, "spend_people_micros": 9_000_000_000,
    "contacted": 1200, "replied": 36, "meetings": 9, "opportunities": 3,
}


# -- the pure part --------------------------------------------------------

def test_the_derived_figures_are_derived_once_and_deterministically():
    b = from_mapping(DECLARED)
    assert b.monthly_spend_micros == 11_300_000_000
    assert b.window_spend_micros == 33_900_000_000
    assert b.cost_per_meeting_micros == 33_900_000_000 // 9
    assert b.cost_per_opportunity_micros == 33_900_000_000 // 3
    assert b.reply_rate == pytest.approx(0.03)
    assert b.digest() == from_mapping(dict(DECLARED)).digest()
    assert len(b.digest()) == 64


def test_the_digest_changes_when_any_field_does():
    """The letter quotes the digest. A field that could change without moving
    it would be a field nobody could check."""
    original = from_mapping(DECLARED).digest()
    for name, value in (("meetings", 10), ("spend_people_micros", 1), ("source", "crm"),
                        ("window_end", "2026-08-31")):
        assert from_mapping({**DECLARED, name: value}).digest() != original, name


def test_no_meetings_is_no_number_not_a_free_meeting():
    b = from_mapping({**DECLARED, "meetings": 0, "opportunities": 0})
    assert b.cost_per_meeting_micros is None
    assert b.cost_per_opportunity_micros is None


@pytest.mark.parametrize("broken, reason", [
    ({"window_end": "2026-05-01"}, "ends before it starts"),
    ({"replied": 5000}, "cannot exceed"),
    ({"meetings": 5000}, "cannot exceed"),
    ({"spend_data_micros": -1}, "negative"),
    ({"source": "guessed"}, "source must be"),
])
def test_a_baseline_that_cannot_be_true_is_refused(broken, reason):
    """A signed typo is worse than no baseline: it is the number the month-9
    conversation is held against."""
    with pytest.raises(BaselineError, match=reason):
        from_mapping({**DECLARED, **broken})


def test_a_missing_field_names_itself():
    with pytest.raises(BaselineError, match="meetings"):
        from_mapping({k: v for k, v in DECLARED.items() if k != "meetings"})


def test_canonical_form_is_stable_across_construction_paths():
    a = from_mapping(DECLARED)
    b = Baseline(window_start=date(2026, 6, 1), window_end=date(2026, 8, 30),
                 spend_tools_micros=1_200_000_000, spend_data_micros=800_000_000,
                 spend_sending_micros=300_000_000, spend_people_micros=9_000_000_000,
                 contacted=1200, replied=36, meetings=9, opportunities=3)
    assert a.canonical() == b.canonical()


# -- written once ---------------------------------------------------------

@requires_db
def test_a_baseline_is_frozen_once(db, tenant):
    captured = from_mapping(DECLARED)
    with db.tenant_tx(str(tenant["id"])) as cur:
        row = baseline_repo.freeze(cur, str(tenant["id"]), captured,
                                   signed_by="R. Ortega", captured_by="operator")
        assert row["digest"] == captured.digest()
        assert row["cost_per_meeting_micros"] == captured.cost_per_meeting_micros
        with pytest.raises(baseline_repo.BaselineAlreadyFrozen):
            baseline_repo.freeze(cur, str(tenant["id"]),
                                 from_mapping({**DECLARED, "meetings": 90}),
                                 signed_by="R. Ortega", captured_by="operator")
        assert baseline_repo.get(cur)["meetings"] == 9, "the second capture changed the row"


@requires_db
def test_a_baseline_is_the_tenants_own(db, tenant, other_tenant):
    with db.tenant_tx(str(tenant["id"])) as cur:
        baseline_repo.freeze(cur, str(tenant["id"]), from_mapping(DECLARED),
                             signed_by="R. Ortega", captured_by="operator")
    with db.tenant_tx(str(other_tenant["id"])) as cur:
        assert baseline_repo.get(cur) is None


# -- through the API --------------------------------------------------------

@pytest.fixture
def client(db):
    return TestClient(create_app(db), raise_server_exceptions=False)


@pytest.fixture
def key(db, tenant):
    return issue_api_key(db, str(tenant["id"]), "test", []).token


def _auth(token: str) -> dict[str, str]:
    return {"x-api-key": token}


@requires_db
def test_the_api_freezes_once_and_reads_back(db, client, key, tenant):
    assert client.get("/v1/baseline", headers=_auth(key)).status_code == 404

    body = {**DECLARED, "signed_by": "R. Ortega"}
    first = client.post("/v1/baseline", json=body, headers=_auth(key))
    assert first.status_code == 201, first.text
    frozen = first.json()
    assert frozen["digest"] == from_mapping(DECLARED).digest()
    assert frozen["signed_by"] == "R. Ortega"
    assert "tenant_id" not in frozen

    again = client.post("/v1/baseline", json={**body, "meetings": 90}, headers=_auth(key))
    assert again.status_code == 409
    assert "captured once" in again.json()["detail"]

    read = client.get("/v1/baseline", headers=_auth(key)).json()
    assert read["meetings"] == 9 and read["digest"] == frozen["digest"]

    with db.tenant_tx(str(tenant["id"])) as cur:
        cur.execute("select detail from audit_log where action = 'baseline.frozen'")
        rows = cur.fetchall()
    assert len(rows) == 1 and rows[0]["detail"]["digest"] == frozen["digest"]


@requires_db
def test_an_impossible_baseline_is_422_with_the_reason(client, key):
    answer = client.post("/v1/baseline", json={**DECLARED, "replied": 5000,
                                               "signed_by": "R. Ortega"},
                         headers=_auth(key))
    assert answer.status_code == 422
    assert "cannot exceed" in answer.text


@requires_db
def test_activating_without_a_baseline_is_noted_where_an_operator_looks(db, client, key, tenant):
    """Decision 35: noted, not refused. The note is in the response and in the
    audit log, and it disappears once the baseline is frozen."""
    from zolts.catalog import load_catalog

    program = load_catalog().programs[0]
    created = client.post("/v1/programs", headers=_auth(key),
                          json={"key": program.key, "version": "1.0.0", "name": program.key,
                                "spec": program.spec, "blueprint": "b2b-saas-sales-led"})
    assert created.status_code == 201, created.text
    program_id = created.json()["id"]

    activated = client.post(f"/v1/programs/{program_id}/activate", headers=_auth(key))
    assert activated.status_code == 200, activated.text
    assert any("no baseline" in line for line in activated.json()["lint"])
    with db.tenant_tx(str(tenant["id"])) as cur:
        cur.execute("select count(*) as n from audit_log"
                    " where action = 'program.activated_without_baseline'")
        assert cur.fetchone()["n"] == 1

    client.post("/v1/baseline", json={**DECLARED, "signed_by": "R. Ortega"},
                headers=_auth(key))
    again = client.post(f"/v1/programs/{program_id}/activate", headers=_auth(key))
    assert again.status_code == 200, again.text
    assert not any("no baseline" in line for line in again.json()["lint"])
