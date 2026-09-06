"""The demo must not be able to lie.

A seeded demo is the one artifact where the temptation to write a good number
directly is strongest, and where being caught doing it ends a sale. These tests
assert the seeder cannot: it has no path to a lift, a significance flag or a
pipeline figure, and every assumption it makes is in its own output.
"""

from __future__ import annotations

import re
from pathlib import Path

SEEDER = Path(__file__).resolve().parent.parent / "scripts" / "seed_demo.py"


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
    """It inserts exactly one kind of row directly — a control-arm conversion,
    which has no send to hang a webhook off — and everything else goes through
    the runtime."""
    inserts = re.findall(r"insert into (\w+)", SEEDER.read_text())
    assert inserts == ["outcome"], inserts


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
