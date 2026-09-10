"""The time-to-touch table in `docs/06` is read, judged and surfaced.

`docs/06` publishes a p95 target per signal tier for three stages and says of
that table: *these are engineering KPIs, not marketing aspirations: they appear
on the customer dashboard and in the contractual SLA of the Scale and
Enterprise plans.* The runtime measured the latency — detection, execution and
total p95, in `runtime/watch.py` and on the console's signals view — and
compared it to nothing at all. An operator read a number with no target beside
it, which is the same as no SLA: a commitment nobody can fail is not a
commitment, and the plan it is written into is sold on it.

Two rules govern every assertion below.

**Behavioural, not a table comparison.** The document's numbers are parsed out
of `docs/06` and each one is checked by building a p95 at the bound and
requiring the verdict to flip there. Comparing the constant in code against the
constant in the document passes on a constant nothing reads, which is this
repository's dominant defect and the reason `test_operating_thresholds.py`
exists in the shape it does.

**A silence is never a pass.** Four of the twelve cells cannot produce a
verdict — two because the document commits to no p95 there, and one column
because this runtime has no probe for it. Each is asserted to be its own
distinct answer, because a missing probe reported as an absent obligation is
how an unmeasured SLA reads as a met one.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime import watch
from runtime.api import console as console_view
from tests.conftest import requires_db
from zolts.latency import MEASURED, TARGETS, Stage, Verdict, judge, target_minutes
from zolts.signals import catalogue

DOC = Path(__file__).resolve().parent.parent / "docs" / "06-signal-library.md"

# The document's column headings, and the stage each one is about. A heading
# the document carries that is not here fails the completeness test below
# rather than being skipped: a column nobody mapped is a column nobody checked.
COLUMNS = {
    "Ingestion → signal available": Stage.AVAILABLE,
    "Signal → action proposed": Stage.PROPOSED,
    "Signal → action executed": Stage.EXECUTED,
}


def _published() -> dict[str, dict[Stage, int | None]]:
    """The table under "Time-to-touch SLA", parsed out of the document."""
    body = DOC.read_text(encoding="utf-8")
    section = body.split("## Time-to-touch SLA", 1)
    assert len(section) == 2, "docs/06 no longer carries a Time-to-touch SLA section"
    rows = [line for line in section[1].splitlines() if line.strip().startswith("|")]
    assert len(rows) >= 6, "the time-to-touch table lost its rows"

    heading = [c.strip() for c in rows[0].strip("|").split("|")]
    assert heading[0] == "Signal tier", heading
    order = [COLUMNS[c] for c in heading[1:]]

    published: dict[str, dict[Stage, int | None]] = {}
    for line in rows[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != len(heading):
            break
        published[cells[0]] = dict(zip(order, (_minutes(c) for c in cells[1:])))
    return published


def _minutes(cell: str) -> int | None:
    """`p95 < 90 min` and `p95 < 2h` become minutes. Prose becomes None."""
    match = re.fullmatch(r"p95 < (\d+)\s*(min|h)", cell)
    if not match:
        return None
    value = int(match.group(1))
    return value if match.group(2) == "min" else value * 60


# -- the document is the source of the numbers ---------------------------


def test_every_column_the_document_publishes_is_mapped_to_a_stage():
    body = DOC.read_text(encoding="utf-8").split("## Time-to-touch SLA", 1)[1]
    heading = [c.strip() for c in
               next(l for l in body.splitlines() if l.strip().startswith("|"))
               .strip("|").split("|")][1:]
    assert set(heading) == set(COLUMNS), (
        "docs/06 renamed or added a time-to-touch column and nothing here reads "
        "it, so a target could move without a test noticing")


def test_every_tier_the_document_publishes_has_targets_in_code():
    assert set(_published()) == set(TARGETS)


@pytest.mark.parametrize("tier", sorted(_published()))
def test_the_published_target_is_the_bound_the_verdict_turns_on(tier):
    """Behavioural: the number is checked by making the verdict flip on it.

    One minute under the published p95 meets, and the published p95 itself
    misses — the document writes every target as a strict `<`, and a strict
    bound enforced loosely is off by exactly the amount a renewal argues about.
    """
    for stage, published in _published()[tier].items():
        if published is None or stage not in MEASURED:
            continue
        assert judge(tier, stage, published - 1) is Verdict.MEETS, (tier, stage)
        assert judge(tier, stage, published) is Verdict.MISSES, (tier, stage)
        assert judge(tier, stage, published + 1) is Verdict.MISSES, (tier, stage)


def test_a_cell_the_document_writes_as_prose_is_not_given_a_number():
    """Tier C batches daily and Tier D defers to a playbook. Neither is a p95."""
    assert target_minutes("C", Stage.EXECUTED) is None
    assert target_minutes("D", Stage.EXECUTED) is None
    assert judge("C", Stage.EXECUTED, 10_000) is Verdict.NO_TARGET
    assert judge("D", Stage.EXECUTED, 10_000) is Verdict.NO_TARGET


def test_the_stage_with_no_probe_says_so_rather_than_passing():
    """The column this runtime cannot measure never reports a pass.

    `docs/06` targets ingestion to signal available. The split the runtime does
    publish — observed to ingested — is the source's own lag, upstream of that
    column. Reporting it here would be a verdict on the wrong quantity, which
    reads exactly like a verdict on the right one.
    """
    assert Stage.AVAILABLE not in MEASURED
    for tier in TARGETS:
        assert target_minutes(tier, Stage.AVAILABLE) is not None, tier
        assert judge(tier, Stage.AVAILABLE, 1) is Verdict.NOT_MEASURED, tier
    assert Verdict.NOT_MEASURED is not Verdict.NO_TARGET


def test_no_data_is_not_a_pass():
    assert judge("A", Stage.EXECUTED, None) is Verdict.NO_DATA


def test_a_tier_this_release_does_not_know_is_not_a_pass():
    assert judge("Z", Stage.EXECUTED, 1) is Verdict.UNKNOWN_TIER
    assert judge(None, Stage.EXECUTED, 1) is Verdict.UNKNOWN_TIER


def test_every_tier_the_catalogue_ships_has_a_target():
    """A shipped signal whose tier has no row would be judged UNKNOWN_TIER."""
    shipped = {d.tier for d in catalogue().values() if d.tier}
    assert shipped, "the catalogue declares no tiers at all"
    assert shipped <= set(TARGETS), sorted(shipped - set(TARGETS))


def test_the_page_says_which_stages_are_measured_and_agrees_with_the_code():
    """ADR-054's mechanism: the status table fails in both directions.

    A stage the page marks **Not measured** that acquires a probe fails here
    until the page is corrected, so the document cannot quietly fall behind the
    runtime — and a stage the page claims is built that has no probe fails too.
    A page that only goes stale in one direction is a page that flatters the
    product, which is the failure `docs/08` and `docs/11` were corrected for.
    """
    body = DOC.read_text(encoding="utf-8")
    section = body.split("**What the runtime measures against that table.**", 1)
    assert len(section) == 2, (
        "docs/06 no longer says which time-to-touch stages the runtime measures")
    claimed: dict[Stage, bool] = {}
    for line in section[1].splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 3 or cells[0] in ("Stage", "---") or cells[0].startswith("-"):
            continue
        if cells[0] not in COLUMNS:
            continue
        claimed[COLUMNS[cells[0]]] = cells[1] == "**Built**"
        assert cells[1] in ("**Built**", "**Not measured**"), cells
    assert set(claimed) == set(COLUMNS.values()), (
        "the status table does not carry a row per stage")
    assert {stage for stage, built in claimed.items() if built} == set(MEASURED)


# -- the hygiene claims that are still designs ---------------------------

ROOT = Path(__file__).resolve().parent.parent


def _defs() -> list[str]:
    """Every function defined under `zolts/` and `runtime/`, by name."""
    import ast
    names = []
    for source in list((ROOT / "zolts").rglob("*.py")) + \
            list((ROOT / "runtime").rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        names += [n.name for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return names


def test_the_self_downgrading_signal_is_marked_not_built_and_is_not_built():
    """ADR-054's mechanism on `docs/06`'s hygiene section.

    The page describes a signal that downgrades itself to `advisory` when its
    lift does not beat baseline. Nothing does it, and the state it would move
    into does not exist. The day it is built this fails, so the page is
    corrected rather than quietly overtaken — the direction that matters,
    because a page describing a control nobody built is what D-90, D-92 and
    D-94 all were.
    """
    from dataclasses import fields

    from zolts.signals import SignalDefinition

    body = DOC.read_text(encoding="utf-8")
    assert "**Noise suppression — Not built (SIG-2).**" in body
    assert not [f.name for f in fields(SignalDefinition)
                if f.name in ("state", "status", "advisory")], (
        "a signal definition can now carry a state, so the downgrade may exist "
        "and docs/06 still calls it not built")
    declared = " ".join(path.read_text(encoding="utf-8")
                        for path in sorted((ROOT / "examples" / "signals").glob("*.yaml")))
    assert "advisory" not in declared


def test_per_signal_cost_and_contribution_are_marked_not_built_and_are_not_built():
    body = DOC.read_text(encoding="utf-8")
    assert "**Signal cost in the P&L — Not built (SIG-3).**" in body
    built = [name for name in _defs()
             if "signal" in name.lower()
             and any(word in name.lower() for word in ("cost", "spend", "contribution"))]
    assert not built, (
        f"{built} looks like the per-signal P&L docs/06 still calls not built")


# -- measured against a real database ------------------------------------

NOW = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)


def _tier_a_signal() -> str:
    for key, definition in sorted(catalogue().items()):
        if definition.tier == "A":
            return key
    pytest.fail("the catalogue ships no Tier A signal, so this cannot be measured")


def _fixture(cur, tenant_id, *, signal_type, proposed_after, executed_after,
             ingested_at=NOW):
    """One signal, one enrolment, one proposal and one sent touch.

    Every timestamp is written rather than defaulted. The measurement is about
    intervals, and a fixture that lets `now()` supply one end of an interval is
    a fixture that measures the clock (the repository's rule on ambient time).
    """
    cur.execute("insert into account (tenant_id, name) values (%s, %s) returning id",
                (tenant_id, "SLA Co"))
    account = cur.fetchone()["id"]
    cur.execute(
        "insert into signal (tenant_id, entity_type, entity_id, type, strength,"
        " half_life_h, source, legal_basis, payload, observed_at, ingested_at)"
        " values (%s,'account',%s,%s,0.5,72,'test','legitimate_interest','{}',"
        " %s, %s) returning id",
        (tenant_id, account, signal_type, ingested_at - timedelta(minutes=3),
         ingested_at))
    signal = cur.fetchone()["id"]
    cur.execute(
        "insert into program (tenant_id, key, version, spec, spec_hash, status)"
        " values (%s,%s,'1.0.0','{}','h','live') returning id",
        (tenant_id, f"sla-{uuid.uuid4().hex[:8]}"))
    program = cur.fetchone()["id"]
    cur.execute(
        "insert into enrollment (tenant_id, program_id, entity_type, entity_id,"
        " variant, state, context, entered_at) values"
        " (%s,%s,'account',%s,'treatment','running',%s,%s) returning id",
        (tenant_id, program, account, f'{{"signal_id": "{signal}"}}', ingested_at))
    enrollment = cur.fetchone()["id"]
    cur.execute(
        "insert into proposal (tenant_id, enrollment_id, program_id, agent,"
        " idempotency_key, state, created_at)"
        " values (%s,%s,%s,'copywriter',%s,'draft',%s)",
        (tenant_id, enrollment, program, uuid.uuid4().hex,
         ingested_at + timedelta(minutes=proposed_after)))
    cur.execute(
        "insert into touch (tenant_id, enrollment_id, channel, idempotency_key,"
        " status, sent_at) values (%s,%s,'email',%s,'sent',%s)",
        (tenant_id, enrollment, uuid.uuid4().hex,
         ingested_at + timedelta(minutes=executed_after)))
    return program


def _row(report, tier):
    return next(r for r in report if r["tier"] == tier)


@requires_db
def test_a_touch_inside_the_tier_target_meets_it(db, tenant):
    signal = _tier_a_signal()
    with db.tenant_tx(tenant['id']) as cur:
        # The premise, asserted rather than assumed: Tier A's published
        # execution bound is an hour, so 30 minutes is inside it and 10 is
        # inside the proposal bound. A fixture that happens to sit inside a
        # target nobody read would pass whatever the target became.
        assert target_minutes("A", Stage.EXECUTED) == 60
        assert target_minutes("A", Stage.PROPOSED) == 10
        _fixture(cur, tenant['id'], signal_type=signal, proposed_after=9,
                 executed_after=30)
        row = _row(watch.sla(cur), "A")
    assert row["stages"]["executed"]["verdict"] == "meets"
    assert row["stages"]["executed"]["p95Minutes"] == 30
    assert row["stages"]["executed"]["targetMinutes"] == 60
    assert row["stages"]["proposed"]["verdict"] == "meets"


@requires_db
def test_a_touch_outside_the_tier_target_misses_it(db, tenant):
    signal = _tier_a_signal()
    with db.tenant_tx(tenant['id']) as cur:
        assert target_minutes("A", Stage.EXECUTED) == 60
        _fixture(cur, tenant['id'], signal_type=signal, proposed_after=90,
                 executed_after=180)
        row = _row(watch.sla(cur), "A")
    assert row["stages"]["executed"]["verdict"] == "misses"
    assert row["stages"]["executed"]["p95Minutes"] == 180
    assert row["stages"]["proposed"]["verdict"] == "misses"


@requires_db
def test_a_tier_with_nothing_on_it_still_appears(db, tenant):
    """A tier that vanishes when it has no data is a tier whose SLA looks met."""
    with db.tenant_tx(tenant['id']) as cur:
        report = watch.sla(cur)
    shipped = {d.tier for d in catalogue().values() if d.tier}
    assert {r["tier"] for r in report} == shipped
    for row in report:
        assert row["stages"]["executed"]["verdict"] == "no_data"
        assert row["stages"]["executed"]["observations"] == 0


@requires_db
def test_the_unmeasured_stage_is_reported_as_unmeasured_not_as_met(db, tenant):
    signal = _tier_a_signal()
    with db.tenant_tx(tenant['id']) as cur:
        _fixture(cur, tenant['id'], signal_type=signal, proposed_after=1,
                 executed_after=1)
        row = _row(watch.sla(cur), "A")
    available = row["stages"]["available"]
    assert available["verdict"] == "not_measured"
    assert available["p95Minutes"] is None
    # The target is still published, because the operator's question is what
    # was promised — hiding it would make an unmeasured commitment look absent.
    assert available["targetMinutes"] == 5


@requires_db
def test_a_signal_the_catalogue_does_not_declare_is_counted_under_no_tier(db, tenant):
    """A type with no definition drops out rather than joining some tier.

    The tier lives on the definition and not on the row, so a signal type this
    release no longer ships has no tier to be counted under. Folding it into
    one would put somebody else's latency in a tier's contractual number.
    """
    with db.tenant_tx(tenant['id']) as cur:
        assert "invented.not_in_catalogue" not in catalogue()
        _fixture(cur, tenant['id'], signal_type="invented.not_in_catalogue",
                 proposed_after=999, executed_after=9999)
        report = watch.sla(cur)
    for row in report:
        assert row["stages"]["executed"]["observations"] == 0, row["tier"]
        assert row["stages"]["executed"]["verdict"] == "no_data", row["tier"]


@requires_db
def test_the_verdict_can_be_read_for_one_programme(db, tenant):
    signal = _tier_a_signal()
    with db.tenant_tx(tenant['id']) as cur:
        slow = _fixture(cur, tenant['id'], signal_type=signal, proposed_after=90,
                        executed_after=600)
        fast = _fixture(cur, tenant['id'], signal_type=signal, proposed_after=1,
                        executed_after=2)
        assert _row(watch.sla(cur, slow), "A")["stages"]["executed"]["verdict"] \
            == "misses"
        assert _row(watch.sla(cur, fast), "A")["stages"]["executed"]["verdict"] \
            == "meets"
        # Unfiltered, the slow one is in the population and the p95 of two
        # observations is the slower: the programme filter narrows rather than
        # being ignored.
        assert _row(watch.sla(cur), "A")["stages"]["executed"]["verdict"] == "misses"


@requires_db
def test_the_console_carries_the_verdict_beside_the_number(db, tenant):
    """The measurement was already on this screen; only the target was missing."""
    signal = _tier_a_signal()
    with db.tenant_tx(tenant['id']) as cur:
        _fixture(cur, tenant['id'], signal_type=signal, proposed_after=90,
                 executed_after=600)
        view = console_view.signals_view(cur)
    assert view["latency"]["touches"] == 1
    assert _row(view["sla"], "A")["stages"]["executed"]["verdict"] == "misses"
