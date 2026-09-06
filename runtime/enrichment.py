"""Buying a missing field, in the order the waterfall says is cheapest.

`zolts/waterfall.py` has held the cost optimiser since the reference core
existed — the ordering that minimises expected cost subject to a coverage
target and an accuracy floor — and nothing in the runtime imported it. This is
the caller. The split is the one billing and deliverability already use: the
reference core decides, the runtime supplies the facts and does the I/O.

**Hit rates are measured, not declared.** A provider's registration carries a
conservative default, used only until that provider has made enough calls in
a cohort for its own record to mean anything. Everything after that comes from
`enrichment_attempt`, which is the per-provider per-cohort matrix `docs/07`
says accrues only with real volume and `docs/15` lists among the assets a
competitor cannot buy. The optimiser gets better at a customer's own segments
by running, which is the whole argument for running it early.

**A miss is not billed to the tenant.** We pay every provider we ask; the
customer pays for a field they got. That is the expensive half of the choice
and it is deliberate: it makes the waterfall's efficiency our margin rather
than the customer's problem, which is exactly what `docs/07` claims the
optimiser is for, and it is the only reading consistent with a product that
sells being on the same side of the table as the CFO. Registered as decision
21 rather than left as an implementation detail somebody discovers on an
invoice.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta, timezone
from typing import Any

from runtime.connectors.dataprovider import (Found, Lookup, ProviderError, cohort_of,
                                             get_provider, unresolved)
from psycopg.errors import UniqueViolation

from runtime.crypto import open_sealed
from runtime.db import one
from zolts.billing import credits_for
from zolts.waterfall import Provider, optimise

# How many calls a provider must have made in a cohort before its own record
# outweighs the number somebody typed at registration. Below this the cell is
# noise: three hits out of four is 75% and means nothing.
MIN_OBSERVATIONS = 30

# How long a complete miss suppresses a retry. Asking every provider again the
# next morning for a phone number that does not exist is the most reliable way
# to spend a data budget on nothing.
MISS_COOLDOWN = timedelta(days=30)

# Which credit each resolved field costs, per `docs/12`.
CREDIT_KIND = {"email": "enrich.email", "phone": "enrich.phone",
               "firmographics": "enrich.firmographics"}


class EnrichmentError(RuntimeError):
    pass


@dataclass(frozen=True)
class Resolved:
    """What the waterfall bought, and what it cost to find out."""
    field: str
    hit: bool
    values: dict[str, Any] = dc_field(default_factory=dict)
    provider: str | None = None
    confidence: float = 0.0
    credits: float = 0.0
    cost_micros: int = 0
    attempts: tuple[str, ...] = ()
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {"field": self.field, "hit": self.hit, "provider": self.provider,
                "confidence": round(self.confidence, 3),
                "credits": float(self.credits), "costMicros": self.cost_micros,
                "providersTried": list(self.attempts), "reason": self.reason}


def _measured(cur, tenant_id: str, field_name: str, cohort: str) -> dict[str, tuple[int, float]]:
    """Observed hit rate per provider in this cohort: key -> (calls, rate)."""
    cur.execute(
        "select provider_key, count(*) as calls,"
        "       avg(case when hit then 1.0 else 0.0 end) as rate"
        "  from enrichment_attempt"
        " where field = %s and cohort = %s"
        " group by provider_key", (field_name, cohort))
    return {r["provider_key"]: (int(r["calls"]), float(r["rate"] or 0.0))
            for r in cur.fetchall()}


def install_declared(cur) -> list[str]:
    """Register a connector for every provider this tenant described.

    A registration row with a mapping in its config is the whole integration —
    the same decision CRMs already make. Without this the CLI could store a
    provider the runtime had no way to call, which is the defect this
    repository keeps finding rather than one to add.
    """
    from runtime.connectors.dataprovider import register_provider
    from runtime.connectors.declarative_provider import DeclarativeDataProvider

    cur.execute("select key, config from data_provider where enabled")
    installed = []
    for row in cur.fetchall():
        mapping = (row["config"] or {}).get("mapping")
        if not mapping:
            continue
        register_provider(DeclarativeDataProvider(document=mapping))
        installed.append(row["key"])
    return installed


def _providers(cur, tenant_id: str, field_name: str, cohort: str) -> list[tuple[dict, Provider]]:
    """The registered providers for a field, priced for the optimiser."""
    cur.execute(
        "select * from data_provider where enabled and %s = any(fields) order by key",
        (field_name,))
    rows = cur.fetchall()
    observed = _measured(cur, tenant_id, field_name, cohort)

    priced = []
    for row in rows:
        calls, rate = observed.get(row["key"], (0, 0.0))
        # The registered default until the provider's own record carries
        # enough calls to be worth more than a guess.
        hit_rate = rate if calls >= MIN_OBSERVATIONS else float(row["default_hit_rate"])
        priced.append((row, Provider(
            key=row["key"],
            unit_cost=int(row["unit_cost_micros"]) / 1_000_000,
            accuracy=float(row["accuracy"]),
            hit_rates={cohort: hit_rate},
            default_hit_rate=float(row["default_hit_rate"]),
            billed_on_miss=bool(row["billed_on_miss"]))))
    return priced


def _recently_attempted(cur, entity_type: str, entity_id: str, field_name: str,
                        now: datetime) -> bool:
    """Whether this was already bought for, recently, and is still unresolved.

    The caller returns before this when the field is present, so reaching here
    means the last attempt produced nothing usable — either nobody had it, or
    somebody had a value we could not attribute. One rule covers both, and
    both cost the same to repeat.
    """
    cur.execute(
        "select max(attempted_at) as last from enrichment_attempt"
        " where entity_type = %s and entity_id = %s and field = %s"
        "   and attempted_at >= %s",
        (entity_type, entity_id, field_name, now - MISS_COOLDOWN))
    row = cur.fetchone()
    return bool(row and row["last"])


def _record(cur, tenant_id: str, *, provider_key: str, field_name: str, cohort: str,
            entity_type: str, entity_id: str, found: Found, legal_basis: str) -> None:
    cur.execute(
        "insert into enrichment_attempt (tenant_id, provider_key, field, cohort,"
        " entity_type, entity_id, hit, confidence, cost_micros, legal_basis)"
        " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant_id, provider_key, field_name, cohort, entity_type, entity_id,
         found.hit, found.confidence if found.hit else None,
         found.cost_micros, legal_basis))


def _apply(cur, entity_type: str, entity_id: str, field_name: str,
           values: dict[str, Any]) -> None:
    """Write what was bought onto the entity.

    Only the columns a field is defined to resolve, and never over a value that
    is already there: a later provider disagreeing with an earlier one is not a
    reason to overwrite a fact somebody may have acted on.
    """
    if field_name == "email":
        cur.execute("update person set email = coalesce(email, %s),"
                    " email_status = coalesce(email_status, 'verified'),"
                    " updated_at = now() where id = %s",
                    (values.get("email"), entity_id))
    elif field_name == "phone":
        cur.execute("update person set phone = coalesce(phone, %s),"
                    " phone_status = coalesce(phone_status, 'verified'),"
                    " updated_at = now() where id = %s",
                    (values.get("phone"), entity_id))
    elif field_name == "firmographics":
        cur.execute(
            "update account set employee_band = coalesce(employee_band, %s),"
            " industry_code = coalesce(industry_code, %s),"
            " country = coalesce(country, %s), updated_at = now() where id = %s",
            (values.get("employee_band"), values.get("industry_code"),
             values.get("country"), entity_id))


def _credential(cur, row: dict[str, Any], secret_key: str | None) -> str | None:
    """Unseal the provider's credential.

    `data_provider.connection_id` has pointed at a stored credential since the
    table existed and the first version of this module passed None regardless,
    so every lookup reached a real endpoint unauthenticated. A real run found
    it in three 401s — and found the error handling correct at the same time,
    because the matrix stayed empty rather than recording three false misses.
    """
    if not row.get("connection_id") or not secret_key:
        return None
    cur.execute("select secret_enc from connection where id = %s and status = 'active'",
                (row["connection_id"],))
    stored = one(cur)
    if stored is None:
        return None
    return open_sealed(stored["secret_enc"], secret_key)


def resolve(cur, tenant: dict[str, Any], *, field_name: str, entity: dict[str, Any],
            account: dict[str, Any] | None = None, legal_basis: str,
            secret_key: str | None = None, now: datetime | None = None) -> Resolved:
    """Buy one field for one entity, cheapest expected order first.

    Returns rather than raises on a miss: not finding a phone number is an
    answer, and the optimiser learns from it.
    """
    from runtime import metering

    if field_name not in CREDIT_KIND:
        raise EnrichmentError(
            f"'{field_name}' is not a priced field: {', '.join(sorted(CREDIT_KIND))}")
    # Enrichment obtains personal data from a third party. A resolved value
    # with no basis recorded beside it is one nobody can defend to a DPO, and
    # the basis cannot be reconstructed afterwards.
    if not legal_basis:
        raise EnrichmentError(
            "enrichment needs a legal basis; obtaining personal data from a "
            "third party without one recorded is not defensible after the fact")

    moment = now or datetime.now(timezone.utc)
    tenant_id = str(tenant["id"])
    entity_type = "account" if field_name == "firmographics" else "person"
    entity_id = str(entity["id"])
    cohort = cohort_of(account if field_name != "firmographics" else entity)

    if not unresolved(entity, field_name):
        return Resolved(field=field_name, hit=False, reason="already resolved")
    if _recently_attempted(cur, entity_type, entity_id, field_name, moment):
        return Resolved(field=field_name, hit=False,
                        reason="already bought for recently, still unresolved")

    # Credits are the thing being sold, and a lookup we cannot bill for is one
    # we should not pay a provider for either.
    budget = metering.allowance(cur, tenant, cost=CREDIT_KIND[field_name])
    if not budget.allowed:
        return Resolved(field=field_name, hit=False, reason=f"budget: {budget.reason}")

    priced = _providers(cur, tenant_id, field_name, cohort)
    if not priced:
        return Resolved(field=field_name, hit=False,
                        reason=f"no provider is registered for {field_name}")

    by_key = {row["key"]: row for row, _ in priced}
    plan = optimise([p for _, p in priced], cohort)
    lookup = Lookup(field=field_name, entity=entity, account=account)

    tried: list[str] = []
    spent = 0
    for key in plan.order:
        row = by_key[key]
        try:
            found = get_provider(key).resolve(
                lookup, credential=_credential(cur, row, secret_key),
                config=dict(row["config"] or {}))
        except ProviderError:
            # An error is the absence of an answer. Counting it as a miss would
            # teach the optimiser that a broken integration is a bad provider.
            tried.append(f"{key}:error")
            continue

        tried.append(key)
        spent += found.cost_micros
        _record(cur, tenant_id, provider_key=key, field_name=field_name, cohort=cohort,
                entity_type=entity_type, entity_id=entity_id, found=found,
                legal_basis=legal_basis)
        if not found.hit:
            continue

        try:
            # A savepoint, because a constraint violation aborts the whole
            # transaction otherwise and one unusable answer would lose the
            # whole batch.
            with cur.connection.transaction():
                _apply(cur, entity_type, entity_id, field_name, found.values)
        except UniqueViolation:
            # The provider had a value and it belongs to somebody else. Writing
            # it would merge two identities, which is worse than not having it:
            # a shared inbox, a wrong match, or three people at one company
            # where the provider knows one address.
            #
            # The attempt stays recorded as a hit — the provider answered, and
            # the matrix should say so — and the tenant is not billed for a
            # field they did not get.
            return Resolved(field=field_name, hit=False, provider=key,
                            cost_micros=spent, attempts=tuple(tried),
                            reason="the value already belongs to another person; "
                                   "writing it would merge two identities")

        credits = metering.meter(cur, tenant, kind=CREDIT_KIND[field_name],
                                 provider=key, cost_micros=spent)
        return Resolved(field=field_name, hit=True, values=found.values, provider=key,
                        confidence=found.confidence, credits=float(credits),
                        cost_micros=spent, attempts=tuple(tried))

    # Nobody had it. We paid; the tenant does not.
    return Resolved(field=field_name, hit=False, cost_micros=spent,
                    attempts=tuple(tried),
                    reason=f"{len(tried)} providers asked, none resolved it")


def matrix(cur, field_name: str | None = None) -> list[dict[str, Any]]:
    """The measured hit-rate matrix, as an operator sees it.

    Reported with the call count beside every rate, because a cell with four
    observations and one with four thousand look identical as percentages and
    only one of them is worth acting on.
    """
    cur.execute(
        "select provider_key, field, cohort, count(*) as calls,"
        "       avg(case when hit then 1.0 else 0.0 end) as rate,"
        "       sum(cost_micros) as spend"
        "  from enrichment_attempt"
        " where (%s::text is null or field = %s)"
        " group by provider_key, field, cohort"
        " order by field, cohort, rate desc", (field_name, field_name))
    return [{"provider": r["provider_key"], "field": r["field"], "cohort": r["cohort"],
             "calls": int(r["calls"]), "hitRate": round(float(r["rate"] or 0), 4),
             "spendEur": round(int(r["spend"] or 0) / 1_000_000, 4),
             "trusted": int(r["calls"]) >= MIN_OBSERVATIONS}
            for r in cur.fetchall()]


def provenance(cur, entity_type: str, entity_id: str) -> list[dict[str, Any]]:
    """Where each value on this entity came from. The DPO's question."""
    cur.execute(
        "select distinct on (field) field, provider_key, confidence, legal_basis,"
        "       attempted_at"
        "  from enrichment_attempt"
        " where entity_type = %s and entity_id = %s and hit"
        " order by field, attempted_at desc", (entity_type, entity_id))
    return [{"field": r["field"], "provider": r["provider_key"],
             "confidence": float(r["confidence"] or 0), "legalBasis": r["legal_basis"],
             "resolvedAt": r["attempted_at"].isoformat()} for r in cur.fetchall()]
