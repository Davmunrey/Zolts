"""Accounts, people and suppression."""

from __future__ import annotations

import json
from typing import Any

from runtime.db import one, rows


def upsert_account(cur, tenant_id: str, **fields: Any) -> dict[str, Any]:
    """Upsert on domain when present, otherwise on CRM id.

    A record with neither is inserted unconditionally: the alternative is to
    match on name, which merges unrelated companies and is unrecoverable.
    """
    conflict = "(tenant_id, domain) where domain is not null" if fields.get("domain") else None
    if conflict is None and fields.get("crm_id"):
        conflict = "(tenant_id, crm_id) where crm_id is not null"
    cols = ["tenant_id", "name", "domain", "legal_id", "country", "employee_band",
            "industry_code", "crm_id", "attributes", "confidence"]
    values = [tenant_id, fields.get("name") or "unknown"] + [
        json.dumps(fields.get(c, {})) if c == "attributes" else fields.get(c)
        for c in cols[2:]
    ]
    values[-1] = fields.get("confidence", 1.0)
    placeholders = ",".join(["%s"] * len(cols))
    if conflict:
        sql = (f"insert into account ({','.join(cols)}) values ({placeholders})"
               f" on conflict {conflict} do update set"
               "   name = coalesce(excluded.name, account.name),"
               "   country = coalesce(excluded.country, account.country),"
               "   employee_band = coalesce(excluded.employee_band, account.employee_band),"
               "   industry_code = coalesce(excluded.industry_code, account.industry_code),"
               "   crm_id = coalesce(excluded.crm_id, account.crm_id),"
               "   attributes = account.attributes || excluded.attributes,"
               "   updated_at = now()"
               " returning *")
    else:
        sql = f"insert into account ({','.join(cols)}) values ({placeholders}) returning *"
    cur.execute(sql, values)
    return one(cur)


def upsert_person(cur, tenant_id: str, **fields: Any) -> dict[str, Any]:
    cols = ["tenant_id", "email", "email_status", "linkedin_urn", "full_name",
            "country", "consent_state", "attributes", "crm_id"]
    values = [tenant_id] + [
        json.dumps(fields.get(c, {})) if c in ("consent_state", "attributes") else fields.get(c)
        for c in cols[1:]
    ]
    placeholders = ",".join(["%s"] * len(cols))
    conflict = "(tenant_id, email) where email is not null" if fields.get("email") else None
    if conflict is None and fields.get("crm_id"):
        conflict = "(tenant_id, crm_id) where crm_id is not null"
    if conflict:
        sql = (f"insert into person ({','.join(cols)}) values ({placeholders})"
               f" on conflict {conflict} do update set"
               "   full_name = coalesce(excluded.full_name, person.full_name),"
               "   email_status = coalesce(excluded.email_status, person.email_status),"
               "   linkedin_urn = coalesce(excluded.linkedin_urn, person.linkedin_urn),"
               "   country = coalesce(excluded.country, person.country),"
               # Consent merges rather than replaces: a partial sync that omits
               # a channel must never be read as consent withdrawn for it.
               "   consent_state = person.consent_state || excluded.consent_state,"
               "   attributes = person.attributes || excluded.attributes,"
               "   crm_id = coalesce(excluded.crm_id, person.crm_id),"
               "   updated_at = now()"
               " returning *")
    else:
        sql = f"insert into person ({','.join(cols)}) values ({placeholders}) returning *"
    cur.execute(sql, values)
    return one(cur)


def link(cur, tenant_id: str, person_id: str, account_id: str, **fields: Any) -> None:
    cur.execute(
        "insert into membership (tenant_id, person_id, account_id, title, seniority,"
        " department, buying_role, started_at) values (%s,%s,%s,%s,%s,%s,%s,%s)"
        " on conflict do nothing",
        (tenant_id, person_id, account_id, fields.get("title"), fields.get("seniority"),
         fields.get("department"), fields.get("buying_role"), fields.get("started_at")),
    )


def get_account(cur, account_id: str) -> dict[str, Any] | None:
    cur.execute("select * from account where id = %s", (account_id,))
    return one(cur)


def get_person(cur, person_id: str) -> dict[str, Any] | None:
    cur.execute("select * from person where id = %s", (person_id,))
    return one(cur)


def contacts_for_account(cur, account_id: str, roles: list[str] | None = None,
                         limit: int = 4) -> list[dict[str, Any]]:
    sql = ("select p.*, m.buying_role, m.title from person p"
           " join membership m on m.person_id = p.id"
           " where m.account_id = %s and m.ended_at is null")
    params: list[Any] = [account_id]
    if roles:
        sql += " and m.buying_role = any(%s)"
        params.append(roles)
    sql += " order by p.created_at limit %s"
    params.append(limit)
    cur.execute(sql, params)
    return rows(cur)


def suppress(cur, tenant_id: str, scope: str, value: str, reason: str, source: str) -> None:
    cur.execute(
        "insert into suppression (tenant_id, scope, value, reason, source)"
        " values (%s,%s,%s,%s,%s) on conflict (tenant_id, scope, value) do nothing",
        (tenant_id, scope, value, reason, source),
    )


def suppressed_keys(cur, email: str | None, domain: str | None) -> set[str]:
    """Return the suppression scopes that match, for the policy engine."""
    cur.execute(
        "select scope from suppression where (scope = 'email' and value = %s)"
        " or (scope = 'domain' and value = %s)",
        (email, domain),
    )
    return {r["scope"] for r in cur.fetchall()}


def _when(value: Any) -> Any:
    """A CRM's empty string is not a timestamp.

    Every provider formats dates differently and Postgres parses all of them;
    what it will not parse is `""`, which is what a CRM sends for a deal with
    no close date. Coerced here rather than in three connectors.
    """
    return None if value in (None, "") else value


def upsert_opportunity(cur, tenant_id: str, *, provider: str, crm_id: str,
                       account_id: str | None = None, **fields: Any) -> dict[str, Any]:
    """Store one deal. Keyed on (tenant, provider, crm id), so a re-sync moves
    a deal from open to won rather than writing a second one.

    `account_id` is nullable and stays nullable: a deal whose account this sync
    has not seen is still a deal, and dropping it would quietly reopen the hole
    the table exists to close.
    """
    cur.execute(
        "insert into opportunity (tenant_id, provider, crm_id, account_id, name, stage,"
        " status, amount_micros, currency, owner, opened_at, closed_at, attributes)"
        " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " on conflict (tenant_id, provider, crm_id) do update set"
        "   account_id = coalesce(excluded.account_id, opportunity.account_id),"
        "   name = coalesce(excluded.name, opportunity.name),"
        "   stage = excluded.stage,"
        "   status = excluded.status,"
        "   amount_micros = coalesce(excluded.amount_micros, opportunity.amount_micros),"
        "   currency = coalesce(excluded.currency, opportunity.currency),"
        "   owner = coalesce(excluded.owner, opportunity.owner),"
        "   opened_at = coalesce(excluded.opened_at, opportunity.opened_at),"
        "   closed_at = excluded.closed_at,"
        "   attributes = opportunity.attributes || excluded.attributes,"
        "   updated_at = now()"
        " returning *",
        (tenant_id, provider, crm_id, account_id, fields.get("name"), fields.get("stage"),
         fields.get("status", "open"), fields.get("amount_micros"), fields.get("currency"),
         fields.get("owner"), _when(fields.get("opened_at")), _when(fields.get("closed_at")),
         json.dumps(fields.get("attributes") or {})),
    )
    return one(cur)

