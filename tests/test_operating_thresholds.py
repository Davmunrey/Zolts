"""The operating thresholds in `docs/09` are read, not restated.

`docs/09` heads its threshold table **"Operating thresholds (non-negotiable)"**
and `zolts/deliverability.py` opens the constant beneath the table with the
comment *"The table in docs/09, as data"*. Nothing checked that it was. It was
not: the reply-rate row names an alarm at 2% and a review at 1%, and only the
review had ever been implemented, so a mailbox replying at 1.5% was in alarm in
the document and healthy in the product.

This is ADR-017's mechanism one document over — the price list lives in code and
a test reads `docs/12` to check it. The same rule applied here, and for a
sharper reason: a price that drifts is invoiced wrongly and can be corrected
afterwards, and a bounce cut-off that drifts burns a sending domain, which
cannot. The module's own docstring says these errors do not surface as a failing
test, which is exactly the argument for making one surface them.

Every assertion here is behavioural. The test builds `Metrics` at the bound the
document names and requires `assess` to return that rule, rather than comparing
two tables of numbers: a table comparison passes on a constant nothing reads,
which is the defect shape this repository keeps finding in itself.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from zolts.deliverability import (ADVISORY, BASE_CAP, THRESHOLDS, Health, Mailbox,
                                  Metrics, Provider, assess, ramp_plan,
                                  reputation_factor, warmup_factor)

DOC = Path(__file__).resolve().parent.parent / "docs" / "09-execution-and-deliverability.md"

# The document's metric names, and the rate on `Metrics` each one is about. A
# name the document uses that is not here fails the completeness test below,
# rather than being skipped: a row nobody mapped is a row nobody checked.
RATES = {
    "Bounce rate": "bounce_rate",
    "Spam complaints": "complaint_rate",
    "Reply rate": "reply_rate",
    "Unsubscribe": "unsubscribe_rate",
}

# How many of a thousand sends produce a given rate, for building `Metrics` at
# a bound. A thousand clears MIN_SAMPLE by twenty times over.
SENT = 1000


def _table() -> list[tuple[str, str, str, str]]:
    """The four cells of every row of the operating-thresholds table."""
    text = DOC.read_text(encoding="utf-8")
    head = text.index("## Operating thresholds")
    body = text[head:text.index("\n## ", head + 1)]
    rows = re.findall(r"^\| ([^|]+?) \| ([^|]+?) \| ([^|]+?) \| ([^|]+?) \|$",
                      body, re.M)
    return [r for r in rows if r[0] not in ("Metric",) and not r[0].startswith("--")]


def _bound(cell: str) -> tuple[float, bool] | None:
    """A threshold cell as (rate, is_floor), or None where the row states none.

    The document's own notation decides the direction: a leading `<` means the
    rule fires *below* the number (a floor, as a reply rate is), and a bare
    number means at or above it (a ceiling, as a bounce rate is). Nothing is
    inferred from the metric's name — the day a fifth row appears it will be
    read the same way.
    """
    match = re.match(r"^(<?)([\d.]+)%", cell.strip())
    if not match:
        return None
    return float(match.group(2)) / 100, match.group(1) == "<"


def _metrics(attr: str, rate: float) -> Metrics:
    """`Metrics` whose `attr` is exactly `rate` and whose every other rate is
    healthy, so the verdict under test is the only one that can fire."""
    counts = {"bounced": 0, "complained": 0, "unsubscribed": 0,
              "replied": int(SENT * 0.05)}
    counts[{"bounce_rate": "bounced", "complaint_rate": "complained",
            "unsubscribe_rate": "unsubscribed", "reply_rate": "replied"}[attr]] = \
        round(SENT * rate)
    return Metrics(sent=SENT, **counts)


def _rows(column: int):
    """Every (metric, bound, is_floor) the given column of the table states."""
    for row in _table():
        if row[0] not in RATES:
            continue
        bound = _bound(row[column])
        if bound is not None:
            yield row[0], row[column], bound[0], bound[1]


# ── the table is read at all ──────────────────────────────────────────────

def test_the_threshold_table_is_still_where_this_file_reads_it():
    """The premise, asserted before any verdict rests on it.

    A regex that matches nothing produces a file of tests that all pass, which
    looks exactly like a table of guards holding. Six defects in `docs/22` have
    been that shape.
    """
    rows = _table()
    assert len(rows) >= 5, f"docs/09 no longer holds a threshold table: {rows}"
    assert set(RATES) <= {r[0] for r in rows}, (
        f"docs/09 renamed a metric this file maps: {sorted(RATES)} vs "
        f"{sorted(r[0] for r in rows)}")


def test_every_rate_row_states_an_alarm_and_a_cut_off():
    for name in RATES:
        row = next(r for r in _table() if r[0] == name)
        assert _bound(row[2]) is not None, f"docs/09 dropped {name}'s alarm"
        assert _bound(row[3]) is not None, f"docs/09 dropped {name}'s cut-off"


# ── every documented bound fires ──────────────────────────────────────────

@pytest.mark.parametrize("column,label", [(2, "alarm"), (3, "cut-off")])
def test_every_documented_bound_is_a_rule_the_runtime_applies(column, label):
    """At the bound the document names, `assess` must stop saying everything is
    clear. This is the assertion the reply-rate alarm failed."""
    for name, cell, rate, is_floor in _rows(column):
        attr = RATES[name]
        at = rate - 0.001 if is_floor else rate
        verdict = assess(_metrics(attr, max(at, 0.0)))
        assert verdict.health is not Health.OK, (
            f"docs/09 puts {name}'s {label} at {cell.strip()} and "
            f"{at:.3%} is {verdict.health.value}: {verdict.rule_key}")
        assert attr.split("_")[0] in verdict.rule_key, (
            f"{name} at its {label} fired '{verdict.rule_key}', a rule about "
            f"something else — the row is masked, not applied")


def test_no_alarm_fires_before_the_document_says_it_does():
    """The other direction. A rule that fires before the document's number is a
    product stricter than the contract it published, which reads to an operator
    as a defect and costs sending capacity nobody agreed to give up.

    Only the alarm column can be checked this way. Below a cut-off the row's own
    alarm is *supposed* to be firing, so the cut-off's early-fire test is the
    narrower one below: it asserts nothing is paused there.
    """
    for name, cell, rate, is_floor in _rows(2):
        attr = RATES[name]
        clear = rate + 0.002 if is_floor else max(rate - 0.002, 0.0)
        verdict = assess(_metrics(attr, clear))
        assert verdict.health is Health.OK, (
            f"docs/09 puts {name}'s alarm at {cell.strip()} and "
            f"{clear:.3%} already fires '{verdict.rule_key}'")


def test_nothing_is_paused_before_its_documented_cut_off():
    """A pause stops a tenant's sending. Arriving early, it stops it for a
    number nobody published."""
    for row in _table():
        if row[0] not in RATES or "paused" not in row[3]:
            continue
        rate, is_floor = _bound(row[3])
        clear = rate + 0.002 if is_floor else max(rate - 0.002, 0.0)
        verdict = assess(_metrics(RATES[row[0]], clear))
        assert verdict.health is not Health.PAUSED, (
            f"docs/09 pauses on {row[0]} at {row[3].strip()} and "
            f"{clear:.3%} is already paused: {verdict.rule_key}")


def test_a_cut_off_that_says_paused_pauses():
    """The cut-off column names its own consequence. Two rows say a program or
    a domain is paused and two say a human reviews; a review that silently
    paused would stop a tenant's sending over a copy problem, and a pause that
    silently became a review would let a burning domain keep sending."""
    for row in _table():
        if row[0] not in RATES:
            continue
        bound = _bound(row[3])
        if bound is None:
            continue
        rate, is_floor = bound
        at = max(rate - 0.001, 0.0) if is_floor else rate
        verdict = assess(_metrics(RATES[row[0]], at))
        if "paused" in row[3]:
            assert verdict.health is Health.PAUSED, (
                f"docs/09 says {row[0]} at its cut-off is paused; "
                f"the runtime says {verdict.health.value}")
        else:
            assert verdict.health is Health.ALARM, (
                f"docs/09 says {row[0]} at its cut-off is reviewed, which is an "
                f"alarm; the runtime says {verdict.health.value}")


def test_every_target_is_stricter_than_its_own_alarm():
    """A target the alarm has already passed is a target nobody can meet."""
    for row in _table():
        if row[0] not in RATES:
            continue
        target, alarm = _bound(row[1]), _bound(row[2])
        if target is None or alarm is None:
            continue
        if alarm[1]:
            assert target[0] > alarm[0], f"{row[0]}: target {row[1]} vs alarm {row[2]}"
        else:
            assert target[0] < alarm[0], f"{row[0]}: target {row[1]} vs alarm {row[2]}"


# ── the volume row, as a property rather than a restatement ───────────────

def _volume_row() -> tuple[int, int, int, int]:
    """(low, high, alarm, cut-off) from the emails-per-mailbox-per-day row."""
    row = next(r for r in _table() if "per mailbox per day" in r[0])
    low, high = (int(n) for n in re.findall(r"\d+", row[1]))
    return low, high, int(row[2].strip()), int(row[3].strip())


def test_the_base_rate_sits_inside_the_documented_target_band():
    low, high, _, _ = _volume_row()
    assert low <= BASE_CAP <= high, (
        f"docs/09 targets {low}-{high} emails per mailbox per day and "
        f"BASE_CAP is {BASE_CAP}")


def test_the_scheduler_cannot_reach_the_documented_alarm():
    """Measured, not restated: the largest capacity any mailbox can be granted,
    over every warm-up day and the best metrics the model rewards.

    This is the honest form of a row the runtime does not enforce directly. The
    document bounds what a mailbox may send in a day; the scheduler's own
    arithmetic already stays under it, and the assertion is that it keeps doing
    so. A change that raised the base rate or the engagement bonus past the
    alarm would fail here rather than on a customer's domain.
    """
    _, _, alarm, cutoff = _volume_row()
    best = Metrics(sent=SENT, bounced=0, complained=0, replied=int(SENT * 0.10))
    assert assess(best).health is Health.OK, "the best case is not a clear one"
    peak = max(Mailbox("a@d", Provider.OTHER, warmup_day=day, metrics=best).capacity
               for day in range(1, 60))
    assert peak < alarm <= cutoff, (
        f"a mailbox can be granted {peak} sends in a day and docs/09 alarms "
        f"at {alarm}")
    assert warmup_factor(60) == 1.0, "warm-up no longer tops out, so peak is not the peak"


def test_a_ramp_never_activates_the_whole_fleet_at_once():
    """The last row states no number, only *never a mass activation*. That is a
    property, and it is the one worth asserting: every domain brought up
    together shares one reputation history, so one mistake takes all of them."""
    row = next(r for r in _table() if "new domains" in r[0])
    assert "mass activation" in row[3], f"docs/09 restated the ramp: {row[3]}"
    for count in range(3, 12):
        plan = ramp_plan(count)
        assert len(plan) > 1, f"{count} domains activate in one wave"
        assert max(c for _, c in plan) < count
        assert sum(c for _, c in plan) == count
        assert [d for d, _ in plan] == sorted({d for d, _ in plan})


# ── the two reply rows mean different things ──────────────────────────────

def test_a_weak_reply_rate_alarms_without_costing_sending_capacity():
    """The reply row is the only one in the table that is not about delivery.

    `reputation_factor` halves a mailbox on any alarm, so implementing the
    document's 2% row as an ordinary alarm would have halved the fleet of every
    tenant whose cold outreach replies at 1.9% — a normal figure — for a
    targeting problem that no amount of sending headroom fixes. The row is
    reported and does not spend capacity, and that is the assertion: without it
    the distinction is a comment.
    """
    weak = _metrics("reply_rate", 0.015)
    verdict = assess(weak)
    assert verdict.rule_key == "reply.alarm"
    assert verdict.health is Health.ALARM
    assert reputation_factor(weak) == 1.0
    assert Mailbox("a@d", Provider.OTHER, warmup_day=60, metrics=weak).capacity == BASE_CAP


def test_a_collapsed_reply_rate_does_cost_capacity():
    """The row below it is not advisory, and the difference is the whole reason
    there are two of them. Engagement under 1% is a signal the mail providers
    read themselves, so it is allowed to reach the reputation multiplier."""
    collapsed = _metrics("reply_rate", 0.005)
    assert assess(collapsed).rule_key == "reply.collapsed"
    assert reputation_factor(collapsed) == 0.5
    assert Mailbox("a@d", Provider.OTHER, warmup_day=60,
                   metrics=collapsed).capacity < BASE_CAP


def test_only_the_reply_alarm_is_advisory():
    """A delivery rule that became advisory would keep a burning mailbox at full
    capacity while the console showed an alarm nobody could act on."""
    assert ADVISORY == {"reply.alarm"}
    for rule_key, attr, limit, health, _ in THRESHOLDS:
        if health is not Health.ALARM:
            continue
        assert reputation_factor(_metrics(attr, limit)) == 0.5, (
            f"'{rule_key}' alarms and costs nothing")
