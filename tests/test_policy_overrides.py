"""The four overrides the schema promises, of which the runtime honoured one.

`spec.policy.overrides` allows `max_touches_per_person_per_week`,
`quiet_hours`, `channels_require_basis` and `lists_check`. The gate read the
first and ignored the other three, so a program declaring that it must not
send between 21:00 and 08:00 sent at three in the morning, and a program
naming an extra suppression list did not check it.

The schema's own sentence is "Only overrides stricter than the tenant policy
are accepted". None were accepted at all.

Ignoring is the worst of the three possible behaviours. Honouring does what
the operator asked; refusing tells them it cannot be done; ignoring lets them
believe they are protected by a rule nothing applies.
"""

from __future__ import annotations

from datetime import datetime, time, timezone

import pytest

from zolts.policy import (PACK_V1, ActionContext, Basis, Contact, Decision,
                          OverrideIsLooser, evaluate, tighten)


def _contact(**kw) -> Contact:
    base = dict(entity_id="e1", country="ES", consent={"email": Basis.LEGITIMATE_INTEREST},
                unsubscribed_channels=frozenset(), suppressed_on=frozenset(),
                touches_this_week=0)
    base.update(kw)
    return Contact(**base)


def _context(**kw) -> ActionContext:
    base = dict(channel="email", now=datetime(2026, 5, 1, 12, tzinfo=timezone.utc),
                local_hour=12)
    base.update(kw)
    return ActionContext(**base)


# -- the direction of stricter --------------------------------------------

def test_a_longer_quiet_window_is_accepted():
    tightened = tighten(PACK_V1["ES"], {"quiet_hours": {"opens": "19:00", "closes": "09:00"}})
    assert tightened.quiet_hours == (time(19, 0), time(9, 0))


def test_a_shorter_quiet_window_is_refused():
    """A program cannot buy itself more sending hours than the jurisdiction
    allows by writing them in its own spec."""
    with pytest.raises(OverrideIsLooser, match="shorter than"):
        tighten(PACK_V1["ES"], {"quiet_hours": {"opens": "23:00", "closes": "07:00"}})


def test_a_half_declared_window_is_refused():
    with pytest.raises(OverrideIsLooser, match="not a window"):
        tighten(PACK_V1["ES"], {"quiet_hours": {"opens": "19:00"}})


def test_demanding_consent_where_the_pack_asks_less_is_accepted():
    tightened = tighten(PACK_V1["ES"], {"channels_require_basis": {"email": "consent"}})
    assert tightened.required_basis["email"] is Basis.CONSENT


def test_accepting_a_weaker_basis_than_the_pack_is_refused():
    """Legitimate interest does not stand in for consent, and a program saying
    it does is asking to be allowed something the jurisdiction refuses."""
    with pytest.raises(OverrideIsLooser, match="does not tighten"):
        tighten(PACK_V1["ES"], {"channels_require_basis": {"whatsapp": "legitimate_interest"}})


def test_extra_suppression_lists_are_added_and_the_pack_s_are_kept():
    """More lists is always stricter. A program cannot drop one by omission."""
    tightened = tighten(PACK_V1["ES"], {"lists_check": ["acme_do_not_contact"]})
    assert "acme_do_not_contact" in tightened.suppression_lists
    assert "robinson_list_es" in tightened.suppression_lists, (
        "a program's own list replaced the jurisdiction's instead of adding to it")


def test_no_overrides_returns_the_pack_untouched():
    assert tighten(PACK_V1["ES"], {}) is PACK_V1["ES"]


# -- what the gate does with them -----------------------------------------

def test_a_programs_quiet_hours_actually_deny_a_send():
    """The defect, stated as a test.

    The ES pack is quiet from 20:00, so 19:00 is a sending hour. A program
    declaring quiet from 19:00 must make it not one — and did not, because
    nothing read the override.
    """
    at_nine_pm = _context(channel="voice", local_hour=19)
    assert evaluate(_contact(consent={"voice": Basis.LEGITIMATE_INTEREST}),
                    at_nine_pm).decision is Decision.ALLOW

    denied = evaluate(_contact(consent={"voice": Basis.LEGITIMATE_INTEREST}), at_nine_pm,
                      overrides={"quiet_hours": {"opens": "19:00", "closes": "09:00"}})
    assert denied.decision is Decision.DENY
    assert denied.rule_key == "quiet_hours"


def test_a_programs_extra_list_actually_suppresses():
    """A program naming its own do-not-contact list had it ignored."""
    on_the_list = _contact(suppressed_on=frozenset({"acme_do_not_contact"}))
    assert evaluate(on_the_list, _context()).decision is Decision.ALLOW

    denied = evaluate(on_the_list, _context(),
                      overrides={"lists_check": ["acme_do_not_contact"]})
    assert denied.decision is Decision.DENY
    assert denied.rule_key == "suppression.acme_do_not_contact"


def test_a_programs_stricter_basis_actually_denies():
    """The contact holds legitimate interest, which satisfies the ES pack. A
    program demanding consent must not be able to send to them."""
    assert evaluate(_contact(), _context()).decision is Decision.ALLOW

    denied = evaluate(_contact(), _context(),
                      overrides={"channels_require_basis": {"email": "consent"}})
    assert denied.decision is Decision.DENY
    assert "basis_required" in denied.rule_key


def test_every_override_the_schema_allows_is_either_applied_or_refused():
    """The guard against this returning. A key the schema permits and the
    runtime silently drops is the defect that was here."""
    import json
    from pathlib import Path

    schema = json.loads(Path("examples/schema/zolts-program.schema.json").read_text())
    allowed = set(schema["properties"]["spec"]["properties"]["policy"]
                  ["properties"]["overrides"]["properties"])
    # `max_touches_per_person_per_week` is applied by the gate through
    # ActionContext rather than by `tighten`, because it is a counter rather
    # than a rule of the jurisdiction.
    handled = {"quiet_hours", "channels_require_basis", "lists_check"}
    assert allowed - handled == {"max_touches_per_person_per_week"}, (
        f"the schema allows {sorted(allowed)} and nothing handles "
        f"{sorted(allowed - handled - {'max_touches_per_person_per_week'})}")
