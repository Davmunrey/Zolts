"""Four checks that ran in one of five publish paths.

`POST /v1/programs` refused a program with no holdout, an audience by
`segment_ref` that nothing resolves, an enrichment field the price list does
not carry, and a policy override that cannot be read as stricter. The other
four ways a program reaches the `program` table ran none of them:

    runtime/cli.py:60             `zolts bootstrap`
    runtime/onboarding.py:238     self-service signup
    scripts/seed_demo.py:152      the demo tenant
    scripts/smoke_runtime.py:94   the smoke run

Signup is the one that matters: it publishes into a live tenant with nobody
watching. The first test below is the one that would have failed before
admission moved to `runtime.repo.programs.publish` — it publishes the way
signup publishes, and the API's own refusal never applied.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from runtime.engine import admission
from runtime.repo import programs
from tests.conftest import requires_db

ADMISSIBLE = {
    "trigger": {"events": [{"signal": "funding.round"}], "window": "30d"},
    "audience": {"sql": "select a.id as account_id from account a"},
    "score": {"floor": 0},
    "route": {"tiers": [{"key": "t1"}]},
    "plays": {"t1": {}},
    "experiment": {"holdout_pct": 10, "unit": "account",
                   "primary_metric": "signed_contract_60d"},
}


def _spec(**changes) -> dict:
    spec = json.loads(json.dumps(ADMISSIBLE))
    for path, value in changes.items():
        if value is None:
            spec.pop(path, None)
        else:
            spec[path] = value
    return spec


def _publish(cur, tenant_id: str, spec: dict, *, key: str = "candidate",
             version: str = "1.0.0") -> dict:
    """Publish exactly as signup does: the repo, with no handler in front."""
    return programs.publish(
        cur, tenant_id, key=key, version=version, spec=spec,
        spec_hash=hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest(),
        status="draft", created_by="signup", metadata={"name": key})


# -- what admission refuses ------------------------------------------------

REFUSALS = [
    ("no holdout", _spec(experiment=None), "declares no holdout"),
    ("a waived holdout with no reason", _spec(experiment={"holdout_pct": 0}),
     "no written justification"),
    ("a segment_ref audience", _spec(audience={"segment_ref": "warm-accounts"}),
     "segments are not implemented"),
    ("an audience that writes", _spec(audience={
        "sql": "delete from account returning id as account_id"}), "modifies data"),
    ("an unpriced enrichment field", _spec(enrich={"person": {"require": ["tech_stack"]}}),
     "price list does not carry"),
    ("a half-declared quiet window",
     _spec(policy={"overrides": {"quiet_hours": {"start": "19:00"}}}), "not a window"),
]


@pytest.mark.parametrize("what, spec, reason",
                         REFUSALS, ids=[case[0] for case in REFUSALS])
def test_admission_refuses_and_says_why(what: str, spec: dict, reason: str):
    """One exception type at the boundary, the diagnosis intact.

    A single type is what stops a caller catching three of the four and
    letting the fourth through. Collapsing the message with it would trade one
    bypass for an unreadable 422.
    """
    with pytest.raises(admission.NotAdmissible, match=reason):
        admission.check(spec, "candidate")


def test_an_admissible_program_passes():
    admission.check(ADMISSIBLE, "candidate")


def test_every_shipped_program_is_admissible():
    """The four programs in `examples/programs` are published by signup, the
    CLI, the demo seed and the smoke run. If admission refused one, every one
    of those paths would break at once."""
    from zolts.catalog import load_catalog

    for program in load_catalog().programs:
        admission.check(program.spec, program.key)


# -- where admission runs --------------------------------------------------

def test_publish_calls_admission_before_it_writes():
    """The regression guard for the defect itself.

    Not a style rule: this asserts the check is inside the function every
    caller goes through, rather than in one caller. Moving it back into a
    handler is exactly what happened the first time.
    """
    source = Path("runtime/repo/programs.py").read_text()
    tree = ast.parse(source)
    publish = next(node for node in tree.body
                   if isinstance(node, ast.FunctionDef) and node.name == "publish")
    calls = [node for node in ast.walk(publish) if isinstance(node, ast.Call)]
    names = [ast.unparse(node.func) for node in calls]
    assert "admission.check" in names, "publish stores a program without admitting it"
    assert names.index("admission.check") < names.index("cur.execute"), \
        "admission must run before the insert, not after"


@requires_db
def test_signups_publish_path_refuses_what_the_api_refuses(db, tenant):
    """The defect, reproduced. Before admission moved, this program was
    accepted through the repo and could then be activated."""
    with db.tenant_tx(tenant["id"]) as cur:
        with pytest.raises(admission.NotAdmissible, match="declares no holdout"):
            _publish(cur, tenant["id"], _spec(experiment=None))
        cur.execute("select count(*) as n from program")
        assert cur.fetchone()["n"] == 0, "a refused program must leave no row"


@requires_db
def test_a_refusal_leaves_the_transaction_usable(db, tenant):
    """Signup publishes several programs in sequence. A refusal that aborted
    the transaction would take the admissible ones down with it — and the
    caller could not record why."""
    with db.tenant_tx(tenant["id"]) as cur:
        with pytest.raises(admission.NotAdmissible):
            _publish(cur, tenant["id"], _spec(audience={"segment_ref": "warm"}),
                     key="refused")
        row = _publish(cur, tenant["id"], ADMISSIBLE, key="accepted")
        assert row["key"] == "accepted"


@requires_db
def test_an_inadmissible_program_can_never_be_activated(db, tenant):
    """Activation reads a row. No row, nothing to activate — which is the
    property that makes the choke point worth having."""
    with db.tenant_tx(tenant["id"]) as cur:
        with pytest.raises(admission.NotAdmissible):
            _publish(cur, tenant["id"], _spec(enrich={"account": {"require": ["funding_history"]}}))
        cur.execute("select count(*) as n from program where status = 'live'")
        assert cur.fetchone()["n"] == 0
