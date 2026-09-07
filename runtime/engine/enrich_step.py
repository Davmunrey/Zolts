"""Buy what the program said it needs, before the step that needs it.

`spec.enrich` is part of every shipped program and was read by nothing. The
waterfall existed, was priced, was measured and had a hit-rate optimiser; the
only way to trigger it was an operator typing `runtime.cli enrich` for one
field of one entity. A program declaring

    enrich:
      person:
        require: [email, phone]
        max_cost_per_contact: 0.60

spent nothing and got nothing, and the sequence sent to whatever contact
details happened to already be there.

**The cap is per subject, not per field.** `max_cost_per_account` is what the
program may spend resolving one account, across every field it asked for. A
per-field cap would let a program with six fields spend six times what its
author intended, which is the reading nobody means.

**A miss is absorbed and does not block.** ADR-021: not finding a phone number
is an answer. The step proceeds with what is known, because a sequence that
stops on a missing optional field is a sequence that stops.

**An unpriced field is refused at publish, not at runtime.** `resolve` raises
on a field the price list does not carry, and discovering that while a worker
is mid-tick turns a configuration mistake into a stalled program. The check
runs where the audience's does.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from decimal import Decimal
from typing import Any

from runtime import enrichment
from runtime.connectors.dataprovider import unresolved
from runtime.repo import entities

# What a program may ask to be bought. Deliberately the fields the price list
# carries and nothing else: a field with no price cannot be billed, and a
# runtime that buys what it cannot bill is one that pays for its customers.
# Decision 33.
BUYABLE = ("email", "phone", "firmographics")

ACCOUNT_FIELDS = ("firmographics",)


class EnrichmentNotPriced(ValueError):
    """A program asked for a field the price list does not carry."""


@dataclass
class Bought:
    """What was spent on one enrollment, and what it returned."""
    fields: list[str] = dc_field(default_factory=list)
    hits: list[str] = dc_field(default_factory=list)
    spent_eur: Decimal = Decimal("0")
    capped: bool = False


def declared(spec: dict[str, Any]) -> dict[str, Any]:
    return spec.get("enrich") or {}


def check(spec: dict[str, Any], program_key: str) -> None:
    """Refuse a program asking for a field nothing can buy or bill."""
    for scope in ("account", "person"):
        block = declared(spec).get(scope) or {}
        for name in block.get("require") or []:
            if name not in BUYABLE:
                raise EnrichmentNotPriced(
                    f"program '{program_key}' asks enrichment to buy '{name}' for the "
                    f"{scope}, which the price list does not carry. Buyable: "
                    f"{', '.join(BUYABLE)}")
            if scope == "account" and name not in ACCOUNT_FIELDS:
                raise EnrichmentNotPriced(
                    f"program '{program_key}' asks for '{name}' on the account; it is "
                    "a person's field")
            if scope == "person" and name in ACCOUNT_FIELDS:
                raise EnrichmentNotPriced(
                    f"program '{program_key}' asks for '{name}' on the person; it is "
                    "an account's field")


def _cap(block: dict[str, Any], key: str) -> Decimal | None:
    value = block.get(key)
    return None if value is None else Decimal(str(value))


def ensure(cur, tenant: dict[str, Any], *, spec: dict[str, Any], program_key: str,
           entity_type: str, entity_id: str, legal_basis: str,
           secret_key: str | None = None) -> Bought:
    """Buy the declared fields for this enrollment's subject.

    Called before the step is planned, so the first send has whatever the
    program said it needed. Everything it spends is metered by `resolve`
    itself; this decides only what to ask for and when to stop asking.
    """
    check(spec, program_key)
    bought = Bought()
    blocks = declared(spec)
    if not blocks:
        return bought

    account = entities.get_account(cur, entity_id) if entity_type == "account" else None
    if account is None and entity_type == "account":
        return bought

    # The account's own fields first: firmographics are what a person's cohort
    # is priced against, so buying them first can make the contact cheaper.
    account_block = blocks.get("account") or {}
    if account is not None and account_block.get("require"):
        _buy_all(cur, tenant, account_block, entity=account, account=account,
                 legal_basis=legal_basis, secret_key=secret_key, bought=bought,
                 cap=_cap(account_block, "max_cost_per_account"))

    person_block = blocks.get("person") or {}
    if person_block.get("require") and account is not None:
        limit = person_block.get("max_contacts_per_account")
        people = entities.contacts_for_account(
            cur, str(account["id"]),
            roles=person_block.get("buying_roles") or None,
            limit=int(limit) if limit is not None else 4)
        for person in people:
            _buy_all(cur, tenant, person_block, entity=person, account=account,
                     legal_basis=legal_basis, secret_key=secret_key, bought=bought,
                     cap=_cap(person_block, "max_cost_per_contact"))
    return bought


def _buy_all(cur, tenant, block, *, entity, account, legal_basis, secret_key,
             bought: Bought, cap: Decimal | None) -> None:
    """Every declared field for one subject, until the subject's cap is spent."""
    spent = Decimal("0")
    for name in block.get("require") or []:
        remaining = None if cap is None else cap - spent
        if remaining is not None and remaining <= 0:
            bought.capped = True
            return
        if not unresolved(entity, name):
            continue
        # The cap goes down to `resolve`, which knows what each provider costs.
        # Enforcing it only here would permit one purchase that crosses it.
        result = enrichment.resolve(
            cur, tenant, field_name=name, entity=entity, account=account,
            legal_basis=legal_basis, secret_key=secret_key,
            budget_micros=None if remaining is None
            else int(remaining * Decimal("1000000")))
        if "over-budget" in " ".join(result.attempts):
            bought.capped = True
        bought.fields.append(name)
        spent += Decimal(str(result.cost_micros)) / Decimal("1000000")
        bought.spent_eur += Decimal(str(result.cost_micros)) / Decimal("1000000")
        if result.hit:
            bought.hits.append(name)
            entity.update(result.values)
