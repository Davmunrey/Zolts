"""The demo must not be able to lie.

A seeded demo is the one artifact where the temptation to write a good number
directly is strongest, and where being caught doing it ends a sale. These tests
assert the seeder cannot: it has no path to a lift, a significance flag or a
pipeline figure, and every assumption it makes is in its own output.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from tests.conftest import requires_db

ROOT = Path(__file__).resolve().parent.parent
SEEDER = ROOT / "scripts" / "seed_demo.py"


MEASURED = {"absLift", "mde", "significant", "pipeline", "lift",
            "treatment_rate", "control_rate"}


def test_the_seeder_never_computes_a_measurement():
    """Every figure a buyer looks at comes out of the product's own code.

    Reading these names is the point — the seeder prints what the console
    computed. Binding one is the thing that must not happen.
    """
    import ast

    tree = ast.parse(SEEDER.read_text())
    bound = {node.id for node in ast.walk(tree)
             if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
    bound |= {node.attr for node in ast.walk(tree)
              if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)}
    offenders = bound & MEASURED
    assert not offenders, (
        f"the seeder binds {sorted(offenders)}; a demo that computes its own "
        "headline is a demo that cannot survive a buyer asking how")


def test_the_measurement_is_projected_from_the_console():
    """Not rebuilt, not adjusted: the same dict the product would serve."""
    source = SEEDER.read_text()
    assert 'view = console.build(' in source
    assert 'for p in view["programs"]' in source


def test_the_seeder_only_writes_outcomes_through_declared_paths():
    """Every direct write is a control-arm conversion, which has no send to
    hang a webhook off. Everything else — replies, deals and the amounts they
    carry — goes through the runtime's own inbound and sync paths.

    Stated as the property rather than as a count: a second control-arm
    outcome (the deal a control account opened on its own) is the same
    exception, and a test that counted rows would have refused it while
    letting an `insert into touch` through if it replaced the first one.
    """
    source = SEEDER.read_text()
    inserts = re.findall(r"insert into (\w+)", source)
    assert set(inserts) == {"outcome"}, inserts
    for match in re.finditer(r"insert into outcome.*?\)\)", source, re.S):
        assert "demo-control" in match.group(0), (
            "a direct insert that is not a control-arm conversion; the treatment arm "
            "has a touch, so its outcomes arrive through the webhook path")


def test_every_chosen_number_is_reported():
    """A buyer asking 'where does this come from' gets the answer from the
    output, not from a conversation."""
    source = SEEDER.read_text()
    assert '"control_reply_rate": CONTROL_REPLY_RATE' in source
    assert '"treatment_reply_rate": TREATMENT_REPLY_RATE' in source
    assert '"tier_capacity_per_week"' in source
    assert '"avg_opportunity_eur"' in source


def test_the_demo_tenant_is_unmistakable():
    source = SEEDER.read_text()
    assert 'slug=f"demo-' in source
    assert "(demo data)" in source


def test_the_policy_summary_is_read_from_the_pack():
    """Not seeded, not hand-written: a buyer can check it against counsel."""
    source = SEEDER.read_text()
    assert "from zolts.policy import PACK_V1" in source
    assert "for c, r in sorted(PACK_V1.items())" in source


# -- the demo, actually seeded ---------------------------------------------

@requires_db
def test_the_demo_seeds_a_tenant_that_enrols_measures_and_leaves_deals_alone(db, monkeypatch):
    """Nothing ran this script. Every test above reads it; none executed it,
    and it had been producing an empty tenant since the audience block began
    to execute (D-50): the seeder passed `industry=` and `upsert_account`
    reads `industry_code`, so the flagship programme's audience matched
    nobody, and the last line crashed handing `console.build` a dictionary of
    labels instead of the tenant's row.

    Small enough for CI and large enough to enrol both arms. What it asserts
    is what a buyer is shown: accounts enrolled, actions sent, a measurement
    computed by the product, and the accounts already in a live deal left
    alone.
    """
    import json
    import subprocess
    import sys

    from tests.conftest import APP_URL, OWNER_URL

    result = subprocess.run(
        [sys.executable, "scripts/seed_demo.py"],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT), "ZOLTS_DEMO_ACCOUNTS": "400",
             "ZOLTS_DATABASE_URL": OWNER_URL, "ZOLTS_APP_DATABASE_URL": APP_URL,
             "ZOLTS_SECRET_KEY": "demo-seed-test-key"})
    assert result.returncode == 0, result.stderr[-3000:]
    out = json.loads(result.stdout)

    seeded = out["seeded"]
    assert seeded["accounts"] == 400
    assert seeded["enrollments"] > 0, (
        "the demo enrolled nobody; its population does not match its own programme's "
        "audience")
    assert seeded["touches_sent"] > 0, "the demo sent nothing"
    assert seeded["open_deals"] >= seeded["left_alone_because_in_a_deal"] > 0

    [program] = out["measurement"]
    assert program["enrolled"] == seeded["enrollments"]
    assert program["metric"], "the programme's primary metric is not reported"
    # Both arms exist, so the screen has something to compare. Whether it
    # resolves at this size is the product's answer, not this test's.
    assert program["unresolvedReason"] is None or "conversions" in program["unresolvedReason"]

    with db.tenant_tx(out["tenant"]["id"]) as cur:
        cur.execute("select count(*) as n from account where industry_code = 'software'")
        assert cur.fetchone()["n"] > 0, "no account carries the column the audience reads"
        # The accounts the CRM delivered already in a deal, none of which may
        # be enrolled. Deals the programme itself produced are open too and
        # arrive after enrolment, so the check names the ones that were there
        # first rather than every open deal.
        cur.execute("select count(*) as n from enrollment e join opportunity o"
                    " on o.account_id = e.entity_id"
                    " where o.status = 'open' and o.crm_id like 'deal-o%'")
        assert cur.fetchone()["n"] == 0, (
            "an account already in an open deal was enrolled anyway")
