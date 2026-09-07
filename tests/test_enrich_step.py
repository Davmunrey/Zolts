"""The program's own enrichment, which nothing ever ran.

`spec.enrich` is in every shipped program. The waterfall existed, was priced,
was measured and had a hit-rate optimiser — and the only way to trigger it was
an operator typing `runtime.cli enrich` for one field of one entity. A program
declaring `require: [email, phone]` spent nothing and got nothing, and the
sequence sent to whatever contact details happened to already be there.

The vocabulary did not match either: the programs asked for `work_email`,
`linkedin_urn`, `tech_stack`, `headcount_by_dept` and `funding_history`, and
`zolts/billing.py` prices `email`, `phone` and `firmographics`. Executing the
block would have raised "not a priced field" for every field of every program.
Decision 33 cut the programs to what can be bought and billed.
"""

from __future__ import annotations

import pytest

from runtime.engine import enrich_step
from tests.conftest import requires_db


def _spec(**blocks) -> dict:
    return {"enrich": blocks}


# -- what a program may ask for -------------------------------------------

def test_a_field_with_no_price_is_refused():
    """A runtime that buys what it cannot bill pays for its customers."""
    with pytest.raises(enrich_step.EnrichmentNotPriced, match="does not carry"):
        enrich_step.check(_spec(person={"require": ["work_email"]}), "demo")


def test_an_account_field_asked_of_a_person_is_refused():
    """`firmographics` describes a company. Asking for it on a person is a
    mistake the schema cannot catch, because the schema does not know which
    fields belong to which entity."""
    with pytest.raises(enrich_step.EnrichmentNotPriced, match="an account's field"):
        enrich_step.check(_spec(person={"require": ["firmographics"]}), "demo")


def test_a_person_field_asked_of_an_account_is_refused():
    with pytest.raises(enrich_step.EnrichmentNotPriced, match="a person's field"):
        enrich_step.check(_spec(account={"require": ["email"]}), "demo")


def test_every_shipped_program_asks_only_for_what_can_be_bought():
    """The guard for decision 33. Before it, all four programs asked for fields
    with no price, so the block could not have run if anything had called it."""
    from pathlib import Path

    from zolts import dsl

    for path in sorted(Path("examples/programs").glob("*.yaml")):
        program = dsl.load(path)
        enrich_step.check(program.spec, program.key)


def test_the_buyable_set_is_exactly_what_the_price_list_carries():
    """Two lists that must agree. If a field is priced and not buyable it can
    never be bought; if it is buyable and not priced, `resolve` raises inside a
    worker tick."""
    from zolts.billing import CREDITS

    priced = {k.split(".", 1)[1] for k in CREDITS if k.startswith("enrich.")}
    assert set(enrich_step.BUYABLE) == priced, (
        f"buyable {sorted(enrich_step.BUYABLE)} against priced {sorted(priced)}")


# -- against a real database ----------------------------------------------

@requires_db
def test_a_declared_field_is_actually_bought(db, tenant):
    """The defect, stated as a test: a program that declares `phone` gets one."""
    from tests.test_enrichment import _register

    from runtime.repo import entities

    with db.tenant_tx(tenant["id"]) as cur:
        _register(cur, "waterfall-a", fields=("phone",), cost_micros=900)
        account = entities.upsert_account(cur, tenant["id"], name="Buyer Co",
                                          industry_code="software",
                                          employee_band="51-200")
        person = entities.upsert_person(cur, tenant["id"], email="p@buyer.example",
                                        full_name="Pat Ruiz", country="ES")
        entities.link(cur, tenant["id"], str(person["id"]), str(account["id"]),
                      buying_role="economic")
        cur.execute("select * from tenant where id = %s", (tenant["id"],))
        tenant_row = dict(cur.fetchone())

        bought = enrich_step.ensure(
            cur, tenant_row, spec=_spec(person={"require": ["phone"]}),
            program_key="demo", entity_type="account", entity_id=str(account["id"]),
            legal_basis="legitimate_interest")

    assert "phone" in bought.fields, "the declared field was never asked for"


@requires_db
def test_the_cap_is_per_subject_not_per_field(db, tenant):
    """`max_cost_per_contact` is what the program may spend resolving one
    contact across every field it asked for. Read per field, a program naming
    six fields would spend six times what its author intended."""
    from tests.test_enrichment import _register

    from runtime.repo import entities

    with db.tenant_tx(tenant["id"]) as cur:
        _register(cur, "waterfall-b", fields=("email", "phone"), cost_micros=400_000)
        account = entities.upsert_account(cur, tenant["id"], name="Capped Co",
                                          industry_code="software")
        person = entities.upsert_person(cur, tenant["id"], full_name="No Contact",
                                        country="ES")
        entities.link(cur, tenant["id"], str(person["id"]), str(account["id"]),
                      buying_role="economic")
        cur.execute("select * from tenant where id = %s", (tenant["id"],))
        tenant_row = dict(cur.fetchone())

        bought = enrich_step.ensure(
            cur, tenant_row,
            spec=_spec(person={"require": ["email", "phone"],
                               "max_cost_per_contact": 0.50}),
            program_key="demo", entity_type="account", entity_id=str(account["id"]),
            legal_basis="legitimate_interest")

    assert bought.capped, "the per-contact cap did not stop the second field"
    assert bought.spent_eur <= 0.80, f"spent {bought.spent_eur} past a 0.50 cap"


@requires_db
def test_a_field_already_present_is_not_bought_again(db, tenant):
    """The cheapest provider is the one you do not call."""
    from tests.test_enrichment import _register

    from runtime.repo import entities

    with db.tenant_tx(tenant["id"]) as cur:
        _register(cur, "waterfall-c", fields=("email",), cost_micros=900)
        account = entities.upsert_account(cur, tenant["id"], name="Known Co",
                                          industry_code="software")
        person = entities.upsert_person(cur, tenant["id"], email="known@co.example",
                                        full_name="Known Person", country="ES")
        entities.link(cur, tenant["id"], str(person["id"]), str(account["id"]),
                      buying_role="economic")
        cur.execute("select * from tenant where id = %s", (tenant["id"],))
        tenant_row = dict(cur.fetchone())

        bought = enrich_step.ensure(
            cur, tenant_row, spec=_spec(person={"require": ["email"]}),
            program_key="demo", entity_type="account", entity_id=str(account["id"]),
            legal_basis="legitimate_interest")

    assert bought.fields == [], "an email that was already there was bought again"
