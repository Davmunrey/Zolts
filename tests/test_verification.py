"""What the measured lift rests on.

An unclassified reply still counts as a conversion. A provider that reports a
reply without a body has told us only that a human responded, so an unsubscribe
written in prose looks exactly like interest — and in cold outreach most
replies are negative.

Flipping that default would move every existing tenant's measured lift on a
deploy, silently. So the over-count is not hidden, it is counted: an outcome
knows whether anybody read the words behind it, and the measurement says how
much of the number rests on ones nobody did. Decision 16.
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import requires_db


def _reply(cur, tenant_id, *, text=None, verdict=None):
    from runtime.agents.triage import Triage
    from runtime.agents.spend import SpendVerdict
    from runtime.engine import inbound

    payload = {"event_type": "reply", "id": f"v-{uuid.uuid4().hex}"}
    if text:
        payload["reply_message"] = {"text": text}
    event = inbound.store(cur, tenant_id, provider="smartlead",
                          signature_ok=True, payload=payload)
    triage = None
    if verdict:
        triage = Triage(verdict=verdict, quote=text or "", text=text or "",
                        spend=SpendVerdict("yes", 0.0, None, None),
                        completion=None, model="m")
    return inbound.apply(cur, tenant_id, event, triage=triage)


@requires_db
def test_an_unread_reply_counts_and_says_it_was_unread(db, tenant):
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _reply(cur, tid)
        cur.execute("select type, verified_by from outcome")
        row = cur.fetchone()
    # Unchanged: it counts.
    assert row["type"] == "reply_positive"
    # And it is marked, so the measurement can say so.
    assert row["verified_by"] is None


@requires_db
def test_a_read_reply_records_who_read_it(db, tenant):
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _reply(cur, tid, text="Happy to talk next week.", verdict="positive")
        cur.execute("select type, verified_by from outcome")
        row = cur.fetchone()
    assert row["type"] == "reply_positive"
    assert row["verified_by"] == "triage"


@requires_db
def test_the_measurement_reports_the_share_nobody_read(db, tenant):
    """The whole point of decision 16: the number says what it rests on."""
    from runtime.api import console

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        cur.execute(
            "insert into program (tenant_id, key, version, spec, spec_hash, status)"
            " values (%s,'p',1,%s,'h','live') returning id",
            (tid, '{"experiment": {"holdout_pct": 10, "primary_metric": "m"}}'))
        program_id = cur.fetchone()["id"]

        for n in range(6):
            cur.execute(
                "insert into enrollment (tenant_id, program_id, entity_type, entity_id,"
                " variant, state) values (%s,%s,'account',gen_random_uuid(),"
                " %s,'running') returning id",
                (tid, program_id, "treatment" if n < 4 else "control"))
            enrollment_id = cur.fetchone()["id"]
            # Two read, four not.
            cur.execute(
                "insert into outcome (tenant_id, enrollment_id, type, occurred_at,"
                " source, verified_by) values (%s,%s,'reply_positive',now(),'t',%s)",
                (tid, enrollment_id, "triage" if n < 2 else None))

        cur.execute("select * from tenant where id = %s", (tid,))
        view = console.build(cur, cur.fetchone())

    program = view["programs"][0]
    assert program["conversions"] == 6
    assert program["unverifiedConversions"] == 4
    assert program["unverifiedShare"] == round(4 / 6, 3)


@requires_db
def test_a_program_with_no_conversions_reports_no_share(db, tenant):
    """Null, not zero. A share of nothing is not zero percent unread."""
    from runtime.api import console

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        cur.execute(
            "insert into program (tenant_id, key, version, spec, spec_hash, status)"
            " values (%s,'q',1,'{}','h','live')", (tid,))
        cur.execute("select * from tenant where id = %s", (tid,))
        view = console.build(cur, cur.fetchone())

    assert view["programs"][0]["unverifiedShare"] is None


@requires_db
def test_a_classified_negative_is_not_a_conversion_at_all(db, tenant):
    """Reading the reply is what makes the difference. Unread, it counted."""
    from runtime.api.console import CONVERSION_TYPES

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _reply(cur, tid, text="We already use a competitor.", verdict="negative")
        cur.execute("select type, verified_by from outcome")
        row = cur.fetchone()
    assert row["type"] == "reply_negative"
    assert row["type"] not in CONVERSION_TYPES
    assert row["verified_by"] == "triage"


def test_the_fixture_shows_the_spread_not_a_flattering_case():
    """A demo where every program is fully read demonstrates nothing. One at
    62% unread beside one at zero is what makes the caveat legible."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from build_fixture import build

    shares = [p["unverifiedShare"] for p in build()["programs"]
              if p.get("unverifiedShare") is not None]
    assert shares, "the fixture reports no unverified share at all"
    assert max(shares) >= 0.5, "no program shows a materially unread number"
    assert min(shares) == 0.0, "no program shows a fully read number for contrast"


def test_the_console_renders_the_share_beside_the_lift():
    """A caveat nobody reaches is a caveat that does not exist."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "design" / "console.html").read_text()
    assert "Conversions read" in source
    # Beside the lift, not in a footnote: the call sits between the arm sizes
    # and the pipeline figure a buyer reads. Positions are taken inside the
    # props block, because the function's own definition appears earlier in
    # the file and comparing against that proves nothing.
    block_start = source.index("<dt>Treatment · control</dt>")
    block = source[block_start:source.index("</dl>", block_start)]
    assert "+ unverifiedRow(p)" in block
    assert block.index("unverifiedRow(p)") < block.index("Incremental pipeline")
