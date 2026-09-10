"""A programme may tighten the archetype it inherits from, and never loosen it.

`docs/05` states the inheritance chain — Blueprint → Industry Pack → Tenant →
Program — and says each level may **add or restrict, never relax policy**. It
calls this the third path between a generic product that needs six weeks of
consulting and a per-customer fork that destroys margin.

`zolts/overlay.py` implements exactly that, raising `PolicyLoosened` on any
lower layer that weakens what it inherits. **Nothing in the runtime called it.**
Two comments referred to it — one in `generate.py` reasoning about why a
discount ceiling belongs to the archetype, one in `programs.publish` saying the
blueprint in a programme's metadata is *not decoration* because *the overlay
resolver needs it* — and the resolver was never reached from either (D-94). The
repository's dominant defect, on the document that explains the product's
central adaptability claim.

It runs at the publish choke point now, which is where the other four admission
checks moved for the same reason: five callers reach a stored programme and only
one of them used to check anything (ADR-037).

**The tenant's blueprint governs, not the programme's metadata.** A programme
may tighten what its archetype permits and never widen it, so the ceiling
belongs to the archetype the customer signed up under — the reading ADR-052
already takes for the discount authority.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import yaml

from tests.conftest import requires_db

ROOT = Path(__file__).resolve().parent.parent


def _spec(**policy_overrides):
    """A minimal admissible programme with the given policy overrides."""
    return {
        "audience": {"sql": "select a.id as account_id from account a"},
        "experiment": {"holdout_pct": 10, "primary_metric": "positive_reply_14d"},
        "policy": {"inherit": "tenant_default", "overrides": dict(policy_overrides)},
        "plays": {"t2": {"steps": [{"channel": "email", "step_key": "s1"}]}},
    }


def _publish(cur, tid, spec, key=None):
    import hashlib
    import json

    from runtime.repo import programs

    body = json.dumps(spec, sort_keys=True)
    return programs.publish(
        cur, tid, key=key or f"p-{uuid.uuid4().hex[:8]}", version="1.0.0", spec=spec,
        spec_hash=hashlib.sha256(body.encode()).hexdigest())


def _tenant_on(db, blueprint_key):
    from runtime.provision import create_tenant

    return create_tenant(db, name=f"t-{uuid.uuid4().hex[:6]}",
                         slug=f"t-{uuid.uuid4().hex[:6]}", region="eu",
                         blueprint_id=blueprint_key)


# ── the premise ───────────────────────────────────────────────────────────

def test_the_archetype_this_file_leans_on_still_declares_what_it_declares():
    """`public-sector` permits one touch a week, which is the tightest
    archetype shipped and the reason it is the one these tests use. A test
    counting to a number the blueprint no longer declares proves nothing."""
    spec = yaml.safe_load((ROOT / "blueprints" / "public-sector.yaml").read_text())
    policy = spec["spec"]["policy"]
    assert policy["max_touches_per_person_per_week"] == 1
    assert "global_suppression" in policy["lists_check"]


# ── the rule ──────────────────────────────────────────────────────────────

@requires_db
def test_a_programme_loosening_its_archetype_is_refused(db):
    """Three a week under an archetype that permits one. Before this, it
    published and ran, and the operator's compliance tier meant nothing."""
    from runtime.engine.admission import NotAdmissible

    tenant = _tenant_on(db, "public-sector")
    with db.tenant_tx(str(tenant["id"])) as cur:
        with pytest.raises(NotAdmissible) as refused:
            _publish(cur, str(tenant["id"]),
                     _spec(max_touches_per_person_per_week=3))
    assert "max_touches_per_person_per_week" in str(refused.value)


@requires_db
def test_a_programme_tightening_its_archetype_is_admitted(db):
    """The other direction, which is the whole point of an overlay: a
    programme may be stricter than the archetype without a fork."""
    tenant = _tenant_on(db, "b2b-saas-sales-led")   # permits three
    with db.tenant_tx(str(tenant["id"])) as cur:
        row = _publish(cur, str(tenant["id"]), _spec(max_touches_per_person_per_week=1))
    assert row["version"] == "1.0.0"


@requires_db
def test_a_programme_matching_its_archetype_is_admitted(db):
    """Equal is not looser. The shipped programmes all sit here, and a first
    version of `policy.tighten` refused every one of them by reading equality
    as a relaxation."""
    tenant = _tenant_on(db, "b2b-saas-sales-led")
    with db.tenant_tx(str(tenant["id"])) as cur:
        row = _publish(cur, str(tenant["id"]), _spec(max_touches_per_person_per_week=3))
    assert row["version"] == "1.0.0"


@requires_db
def test_dropping_a_suppression_list_the_archetype_names_is_refused(db):
    """A superset is stricter. A programme that checks fewer lists than its
    archetype has quietly removed a check somebody is relying on."""
    from runtime.engine.admission import NotAdmissible

    tenant = _tenant_on(db, "local-services-multisite")   # checks two lists
    with db.tenant_tx(str(tenant["id"])) as cur:
        with pytest.raises(NotAdmissible) as refused:
            _publish(cur, str(tenant["id"]), _spec(lists_check=["global_suppression"]))
    assert "lists_check" in str(refused.value)


@requires_db
def test_a_weaker_legal_basis_than_the_archetype_requires_is_refused(db):
    """`ecommerce-dtc` requires consent for email. A programme asking for
    legitimate interest is asking for less, under an archetype whose whole
    reason for existing is that consumers consented."""
    from runtime.engine.admission import NotAdmissible

    tenant = _tenant_on(db, "ecommerce-dtc")
    with db.tenant_tx(str(tenant["id"])) as cur:
        with pytest.raises(NotAdmissible) as refused:
            _publish(cur, str(tenant["id"]),
                     _spec(channels_require_basis={"email": "legitimate_interest"}))
    assert "email" in str(refused.value) or "basis" in str(refused.value).lower()


# ── what must keep working ────────────────────────────────────────────────

@requires_db
def test_a_tenant_with_no_blueprint_publishes_as_before(db, tenant):
    """Nothing to inherit from is not a ceiling of zero. Refusing every
    programme of such a tenant would make the overlay a gate rather than an
    inheritance.

    The premise is cleared here rather than assumed: the shared `tenant`
    fixture *does* declare an archetype, so a first version of this test
    published nine touches a week under one permitting three and read the
    correct refusal as a defect in the fix. A test that assumes the ambient
    fixture's shape is testing the fixture (D-73).
    """
    tid = str(tenant["id"])
    with db.admin_tx() as cur:
        cur.execute("update tenant set blueprint_id = '' where id = %s", (tid,))
        cur.execute("select blueprint_id from tenant where id = %s", (tid,))
        assert not cur.fetchone()["blueprint_id"], "the premise did not hold"
    try:
        with db.tenant_tx(tid) as cur:
            row = _publish(cur, tid, _spec(max_touches_per_person_per_week=9))
    finally:
        with db.admin_tx() as cur:
            cur.execute("update tenant set blueprint_id = 'b2b-saas-sales-led'"
                        " where id = %s", (tid,))
    assert row["version"] == "1.0.0"


@requires_db
def test_a_tenant_naming_an_archetype_this_release_does_not_ship(db):
    """Same answer, different cause. A blueprint key from a future release is
    not a policy of nothing; it is a policy this code cannot read, and refusing
    on it would make an upgrade path a wall."""
    tenant = _tenant_on(db, "archetype-from-the-future")
    with db.tenant_tx(str(tenant["id"])) as cur:
        row = _publish(cur, str(tenant["id"]), _spec(max_touches_per_person_per_week=9))
    assert row["version"] == "1.0.0"


@requires_db
@pytest.mark.parametrize("path", sorted((ROOT / "examples" / "programs").glob("*.yaml")),
                         ids=lambda p: p.stem)
def test_every_shipped_programme_is_admissible_under_its_own_archetype(db, path):
    """The check that would have caught this being too strict.

    `policy.tighten`'s first version refused all four shipped programmes by
    reading equality as a relaxation, and a rule that refuses the product's own
    examples is a rule nobody can ship. Each programme is published under the
    archetype its blueprint names.
    """
    import hashlib
    import json

    from runtime.repo import programs

    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    spec = document["spec"]
    blueprint = (document.get("metadata") or {}).get("blueprint")
    assert blueprint, f"{path.name} names no blueprint, so this test proves nothing"

    tenant = _tenant_on(db, blueprint)
    body = json.dumps(spec, sort_keys=True)
    with db.tenant_tx(str(tenant["id"])) as cur:
        row = programs.publish(
            cur, str(tenant["id"]), key=document["metadata"]["key"], version="9.9.9",
            spec=spec, spec_hash=hashlib.sha256(body.encode()).hexdigest())
    assert row is not None
