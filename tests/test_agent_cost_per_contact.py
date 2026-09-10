"""What the model layer costs per contact reached, against `docs/08`'s target.

`docs/08` sets a **cost target under EUR 0.02 in tokens per contact touched**
and marked it *not measured*. Every model call is priced before it is made and
recorded after it, so the number was computable per programme and per period
and nothing computed it (AGENT-4).

It is the margin number of the agent layer. Reaching a contact for two cents of
tokens and reaching them for twenty look identical on every screen the console
had, and only one of the two has a gross margin.

The target is parsed out of the document and checked behaviourally — the
verdict has to flip at the published number — rather than compared against a
constant, which passes on a constant nothing reads.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime import metering
from tests.conftest import requires_db
from zolts import agentcost
from zolts.agentcost import MODEL_KINDS, TARGET_EUR_PER_CONTACT, Verdict, judge
from zolts.billing import CREDITS

DOC = Path(__file__).resolve().parent.parent / "docs" / "08-ai-agent-layer.md"
NOW = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)


def _published_target() -> float:
    """The euro figure `docs/08` sets for a contact touched."""
    body = DOC.read_text(encoding="utf-8")
    found = re.search(
        r"[Cc]ost target under €([0-9.]+) in tokens per contact touched", body)
    assert found, "docs/08 no longer states a cost target per contact touched"
    return float(found.group(1))


# -- the document is the source of the number ----------------------------


def test_the_target_in_code_is_the_one_the_document_publishes():
    assert TARGET_EUR_PER_CONTACT == _published_target()


def test_the_published_target_is_the_bound_the_verdict_turns_on():
    """Behavioural. `docs/08` writes *under*, so exactly the target misses."""
    target = _published_target()
    assert judge(target - 0.0001) is Verdict.MEETS
    assert judge(target) is Verdict.MISSES
    assert judge(target + 0.0001) is Verdict.MISSES


def test_no_contact_reached_is_not_a_pass():
    assert judge(None) is Verdict.NO_DATA


def test_only_the_model_kinds_count_towards_a_token_target():
    """Five of the eight priced actions buy something other than tokens.

    A denominator of contacts against a numerator carrying a data supplier's
    invoice is a different number wearing this one's name.
    """
    assert MODEL_KINDS <= set(CREDITS), sorted(MODEL_KINDS - set(CREDITS))
    assert MODEL_KINDS == {"agent.generate", "agent.dossier"}
    for kind in ("enrich.email", "enrich.phone", "enrich.firmographics",
                 "signal.check", "email.send", "program.step"):
        assert kind not in MODEL_KINDS, kind


def test_the_page_no_longer_calls_it_unmeasured():
    """ADR-054's mechanism: the page cannot stay behind the runtime.

    The row said *not measured*. It is measured now, and this fails until the
    page says so — the same both-directions guard `docs/06` and `docs/11` carry.
    """
    body = DOC.read_text(encoding="utf-8")
    row = next(line for line in body.splitlines()
               if "per contact touched" in line)
    assert "*not measured*" not in row, (
        "docs/08 still calls the cost target unmeasured, and it is measured")
    assert "AGENT-4" not in row or "Built" in row, row


# -- measured against a real database ------------------------------------


def _reached(cur, tenant_id, *, people, micros_per_call, calls,
             kind="agent.generate", touches_per_person=1, orphan_touches=0):
    """A programme, some people it reached, and the model spend behind them."""
    cur.execute("insert into program (tenant_id, key, version, spec, spec_hash,"
                " status) values (%s,%s,'1.0.0','{}','h','live') returning id",
                (tenant_id, f"cost-{uuid.uuid4().hex[:8]}"))
    program = cur.fetchone()["id"]
    cur.execute("insert into account (tenant_id, name) values (%s,'Cost Co')"
                " returning id", (tenant_id,))
    account = cur.fetchone()["id"]
    cur.execute(
        "insert into enrollment (tenant_id, program_id, entity_type, entity_id,"
        " variant, state, entered_at) values (%s,%s,'account',%s,'treatment',"
        "'running',%s) returning id", (tenant_id, program, account, NOW))
    enrollment = cur.fetchone()["id"]

    for index in range(people):
        cur.execute(
            "insert into person (tenant_id, full_name, email)"
            " values (%s,%s,%s) returning id",
            (tenant_id, f"Person {index}",
             f"p{index}-{uuid.uuid4().hex[:6]}@example.com"))
        person = cur.fetchone()["id"]
        for touch in range(touches_per_person):
            cur.execute(
                "insert into touch (tenant_id, enrollment_id, person_id, channel,"
                " idempotency_key, status, sent_at, direction)"
                " values (%s,%s,%s,'email',%s,'sent',%s,'out')",
                (tenant_id, enrollment, person, uuid.uuid4().hex, NOW))
    for _ in range(orphan_touches):
        cur.execute(
            "insert into touch (tenant_id, enrollment_id, channel,"
            " idempotency_key, status, sent_at, direction)"
            " values (%s,%s,'email',%s,'sent',%s,'out')",
            (tenant_id, enrollment, uuid.uuid4().hex, NOW))
    for _ in range(calls):
        cur.execute(
            "insert into cost_event (tenant_id, program_id, kind, provider,"
            " units, cost_micros, occurred_at) values (%s,%s,%s,'model',1,%s,%s)",
            (tenant_id, program, kind, micros_per_call, NOW))
    return str(program)


@requires_db
def test_spend_inside_the_target_meets_it(db, tenant):
    """Ten contacts, one cent of tokens each. Half the target."""
    with db.tenant_tx(tenant["id"]) as cur:
        assert TARGET_EUR_PER_CONTACT == 0.02, "the premise: two cents"
        program = _reached(cur, tenant["id"], people=10, calls=10,
                           micros_per_call=10_000)
        report = metering.cost_per_contact(cur, program)
    assert report["contactsTouched"] == 10
    assert report["tokenSpendEur"] == 0.10
    assert report["eurPerContact"] == 0.01
    assert report["verdict"] == "meets"


@requires_db
def test_spend_over_the_target_misses_it(db, tenant):
    with db.tenant_tx(tenant["id"]) as cur:
        program = _reached(cur, tenant["id"], people=10, calls=10,
                           micros_per_call=50_000)
        report = metering.cost_per_contact(cur, program)
    assert report["eurPerContact"] == 0.05
    assert report["verdict"] == "misses"


@requires_db
def test_a_contact_reached_three_times_is_one_contact(db, tenant):
    """The denominator is a person, not a touch and not an enrolment."""
    with db.tenant_tx(tenant["id"]) as cur:
        program = _reached(cur, tenant["id"], people=4, calls=4,
                           micros_per_call=10_000, touches_per_person=3)
        report = metering.cost_per_contact(cur, program)
    assert report["contactsTouched"] == 4, "twelve touches reached four people"
    assert report["eurPerContact"] == 0.01


@requires_db
def test_only_the_model_kinds_reach_the_numerator(db, tenant):
    """Enrichment on the same programme must not move a token number."""
    with db.tenant_tx(tenant["id"]) as cur:
        program = _reached(cur, tenant["id"], people=10, calls=10,
                           micros_per_call=10_000)
        cur.execute(
            "insert into cost_event (tenant_id, program_id, kind, provider,"
            " units, cost_micros, occurred_at)"
            " values (%s,%s,'enrich.email','hunter',1,900_000,%s)",
            (tenant["id"], program, NOW))
        report = metering.cost_per_contact(cur, program)
    assert report["eurPerContact"] == 0.01, (
        "a data supplier's invoice reached a target about tokens")
    assert report["verdict"] == "meets"


@requires_db
def test_a_touch_naming_nobody_is_excluded_and_reported(db, tenant):
    """It overstates the cost, which is the direction that cannot flatter.

    A sent touch predating `touch.person_id` names nobody, so it cannot enter a
    count of distinct people. Dropping it silently would leave a number reading
    high with no reason on the page, so the count travels with the verdict.
    """
    with db.tenant_tx(tenant["id"]) as cur:
        program = _reached(cur, tenant["id"], people=2, calls=4,
                           micros_per_call=10_000, orphan_touches=3)
        report = metering.cost_per_contact(cur, program)
    assert report["contactsTouched"] == 2
    assert report["touchesNamingNobody"] == 3
    assert report["eurPerContact"] == 0.02, "four cents over two people"
    assert report["verdict"] == "misses", "exactly the target is not under it"


@requires_db
def test_nothing_reached_yet_is_no_data_rather_than_free(db, tenant):
    with db.tenant_tx(tenant["id"]) as cur:
        program = _reached(cur, tenant["id"], people=0, calls=3,
                           micros_per_call=10_000)
        report = metering.cost_per_contact(cur, program)
    assert report["contactsTouched"] == 0
    assert report["eurPerContact"] is None
    assert report["verdict"] == "no_data"
    assert report["tokenSpendEur"] == 0.03, "the spend happened; the reach did not"


@requires_db
def test_the_number_can_be_read_for_one_programme_and_for_all(db, tenant):
    with db.tenant_tx(tenant["id"]) as cur:
        cheap = _reached(cur, tenant["id"], people=10, calls=10,
                         micros_per_call=10_000)
        dear = _reached(cur, tenant["id"], people=1, calls=1,
                        micros_per_call=500_000)
        assert metering.cost_per_contact(cur, cheap)["verdict"] == "meets"
        assert metering.cost_per_contact(cur, dear)["verdict"] == "misses"
        both = metering.cost_per_contact(cur)
    assert both["contactsTouched"] == 11
    assert both["tokenSpendEur"] == 0.60
    assert both["verdict"] == "misses", "0.60 over 11 contacts is five cents each"


@requires_db
def test_a_window_narrows_both_halves_together(db, tenant):
    """Spend and reach have to be counted over the same period.

    Counting a quarter's tokens against a week's contacts is the shape D-69
    already cost once: an unwindowed denominator under a windowed numerator.
    """
    with db.tenant_tx(tenant["id"]) as cur:
        program = _reached(cur, tenant["id"], people=10, calls=10,
                           micros_per_call=10_000)
        inside = metering.cost_per_contact(cur, program, since=NOW - timedelta(days=1))
        outside = metering.cost_per_contact(cur, program, since=NOW + timedelta(days=1))
    assert inside["contactsTouched"] == 10 and inside["modelCalls"] == 10
    assert outside["contactsTouched"] == 0 and outside["modelCalls"] == 0
    assert outside["verdict"] == "no_data"
