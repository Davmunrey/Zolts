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
    tightened = tighten(PACK_V1["ES"], {"quiet_hours": {"start": "19:00", "end": "09:00"}})
    assert tightened.quiet_hours == (time(19, 0), time(9, 0))


def test_a_shorter_quiet_window_does_not_shorten_the_packs():
    """A program cannot buy itself more sending hours than the jurisdiction
    allows by writing them in its own spec. It is not refused for trying — a
    program shipped for six countries states one baseline — but the longer
    window is what applies."""
    tightened = tighten(PACK_V1["ES"], {"quiet_hours": {"start": "23:00", "end": "07:00"}})
    assert tightened.quiet_hours == PACK_V1["ES"].quiet_hours


def test_a_half_declared_window_is_refused():
    with pytest.raises(OverrideIsLooser, match="not a window"):
        tighten(PACK_V1["ES"], {"quiet_hours": {"start": "19:00"}})


def test_demanding_consent_where_the_pack_asks_less_is_accepted():
    tightened = tighten(PACK_V1["ES"], {"channels_require_basis": {"email": "consent"}})
    assert tightened.required_basis["email"] is Basis.CONSENT


def test_a_weaker_basis_than_the_pack_does_not_weaken_it():
    """Legitimate interest does not stand in for consent. The ES pack requires
    consent for WhatsApp, and a program asking for less keeps the pack's."""
    tightened = tighten(PACK_V1["ES"],
                        {"channels_require_basis": {"whatsapp": "legitimate_interest"}})
    assert tightened.required_basis["whatsapp"] is Basis.CONSENT


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
                      overrides={"quiet_hours": {"start": "19:00", "end": "09:00"}})
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


# -- the shipped programs -------------------------------------------------

def test_every_shipped_programs_policy_block_is_accepted():
    """The guard that would have caught this before it was pushed.

    The first version of `tighten` read `opens`/`closes` — the vocabulary the
    *schedule* block uses for sending windows — while every program writes
    `start`/`end`. It also refused a program for restating a basis the pack
    already requires. Both meant the runtime rejected all four shipped
    programs, and only the end-to-end smoke run noticed.
    """
    from pathlib import Path

    from zolts import dsl

    refused = []
    for path in sorted(Path("examples/programs").glob("*.yaml")):
        program = dsl.load(path)
        overrides = (program.spec.get("policy") or {}).get("overrides") or {}
        for country, rule in PACK_V1.items():
            try:
                tighten(rule, overrides)
            except OverrideIsLooser as exc:
                refused.append(f"{path.name} under {country}: {exc}")
    assert not refused, "a shipped program's policy block is refused:\n  " + "\n  ".join(refused)


def test_a_timezone_nobody_implements_is_refused():
    """Quiet hours are evaluated in the contact's local time. A program naming
    another zone would be silently evaluated in the contact's anyway."""
    with pytest.raises(OverrideIsLooser, match="not implemented"):
        tighten(PACK_V1["ES"], {"quiet_hours": {"start": "19:00", "end": "08:00",
                                                "tz": "Europe/Madrid"}})


def test_restating_the_packs_own_basis_is_not_a_relaxation():
    """Programs write the requirement into their own spec so it is visible
    there. Saying something true is not an override."""
    tightened = tighten(PACK_V1["ES"],
                        {"channels_require_basis": {"email": "legitimate_interest"}})
    assert tightened.required_basis["email"] is Basis.LEGITIMATE_INTEREST
