"""Signing up a tenant without a shell.

Creating a tenant required database access, so every partner cost founder time
and the product could not be sold without a human in the loop.

The shape is an invitation rather than open signup, and that is a decision
rather than a shortcut. Open signup would put an unauthenticated tenant-creating
endpoint on the internet for a product sold to a handful of design partners; the
invitation keeps the decision of who gets in with the operator while moving the
work to the partner. Minting one is a CLI command and has no HTTP surface at
all, because an operator capability reachable from a tenant's key is a
privilege escalation waiting to be found.

Exactly one unauthenticated write path exists in this runtime, and it is
`redeem` below.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from runtime.crypto import hash_token
from runtime.db import Database, one

TOKEN_PREFIX = "zi_"
DEFAULT_TTL_DAYS = 14
MAX_SLUG_ATTEMPTS = 50


class InvitationError(RuntimeError):
    """Raised for every rejected redemption, with the same message.

    An invalid token, an expired one and an already-redeemed one are
    indistinguishable to the caller on purpose: telling them apart tells an
    attacker which tokens exist.
    """


REJECTED = "this invitation is not valid"


@dataclass(frozen=True)
class Invitation:
    token: str
    id: str
    prefix: str
    expires_at: datetime


def _new_token() -> tuple[str, str, str]:
    body = secrets.token_urlsafe(32)
    token = f"{TOKEN_PREFIX}{body}"
    return token, hash_token(token), token[: len(TOKEN_PREFIX) + 6]


def slugify(name: str) -> str:
    """A URL-safe stem from a company name.

    Deliberately lossy and deliberately not unique: uniqueness is settled by
    the database, which is the only place that can settle it without a race.
    """
    stem = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return (stem or "tenant")[:40]


def mint(db: Database, *, company_name: str, email: str | None = None,
         region: str = "eu", blueprint_id: str | None = None,
         ttl_days: int = DEFAULT_TTL_DAYS, created_by: str = "cli") -> Invitation:
    """Issue an invitation. The token is returned once and never recoverable."""
    token, token_hash, prefix = _new_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)
    with db.admin_tx() as cur:
        cur.execute(
            "insert into invitation (token_hash, prefix, email, company_name, region,"
            " blueprint_id, expires_at, created_by)"
            " values (%s,%s,%s,%s,%s,%s,%s,%s) returning id, expires_at",
            (token_hash, prefix, email, company_name, region, blueprint_id,
             expires_at, created_by))
        row = one(cur)
    return Invitation(token=token, id=str(row["id"]), prefix=prefix,
                      expires_at=row["expires_at"])


def describe(db: Database, token: str) -> dict[str, Any]:
    """What the signup form should show, without redeeming anything.

    Reveals only what the operator already told the invitee. A rejected token
    is rejected the same way here as in `redeem`.
    """
    with db.admin_tx() as cur:
        cur.execute(
            "select company_name, region, blueprint_id, expires_at from invitation"
            " where token_hash = %s and redeemed_at is null and expires_at > now()",
            (hash_token(token),))
        row = one(cur)
    if row is None:
        raise InvitationError(REJECTED)
    return {"company_name": row["company_name"], "region": row["region"],
            "blueprint_id": row["blueprint_id"],
            "expires_at": row["expires_at"].isoformat()}


def _claim(cur, token: str) -> dict[str, Any]:
    """Lock the invitation for this transaction, or fail.

    Marking it redeemed here and naming its tenant later would leave a row that
    is half-redeemed, which the table's own check constraint refuses — rightly,
    because a committed row in that state is a signup nobody can interpret. So
    the lock is taken first and both columns are written at once, once the
    tenant exists.

    `for update` is the guard against a concurrent redemption. The second
    transaction blocks here, and when it proceeds it re-evaluates the
    predicate against the committed row, sees `redeemed_at` set, and matches
    nothing.
    """
    cur.execute(
        "select id, company_name, region, blueprint_id from invitation"
        " where token_hash = %s and redeemed_at is null and expires_at > now()"
        " for update",
        (hash_token(token),))
    row = one(cur)
    if row is None:
        raise InvitationError(REJECTED)
    return row


def _unique_slug(cur, stem: str) -> str:
    cur.execute("select 1 from tenant where slug = %s", (stem,))
    if cur.fetchone() is None:
        return stem
    for _ in range(MAX_SLUG_ATTEMPTS):
        # Three bytes is six hex characters. A mutation-coverage run flagged the
        # 3 as unkilled, and it is: `slug` is `text not null unique` with no
        # length ceiling, so any suffix length collides no more often than the
        # loop can absorb, and no behaviour this suite can reach tells six hex
        # characters from eight. An equivalent mutant, answered here rather than
        # with a test that would assert the length of a random string.
        candidate = f"{stem[:32]}-{secrets.token_hex(3)}"
        cur.execute("select 1 from tenant where slug = %s", (candidate,))
        if cur.fetchone() is None:
            return candidate
    raise InvitationError(REJECTED)


def redeem(db: Database, token: str, *, name: str | None = None,
           blueprint_id: str | None = None) -> dict[str, Any]:
    """Turn an invitation into a tenant, a first key and its starter programs.

    Everything up to the tenant row happens in one transaction, so a failure
    part-way leaves neither a consumed invitation nor an orphaned tenant.
    """
    from runtime.provision import issue_api_key

    with db.admin_tx() as cur:
        invitation = _claim(cur, token)
        company = (name or invitation["company_name"]).strip()
        blueprint = blueprint_id or invitation["blueprint_id"] or "b2b-saas-sales-led"
        _assert_known_blueprint(blueprint)
        slug = _unique_slug(cur, slugify(company))
        cur.execute(
            "insert into tenant (slug, name, region, blueprint_id, compliance_tier)"
            " values (%s,%s,%s,%s,'standard') returning *",
            (slug, company, invitation["region"], blueprint))
        tenant = one(cur)
        cur.execute(
            "update invitation set redeemed_at = now(), redeemed_tenant_id = %s"
            " where id = %s and redeemed_at is null",
            (tenant["id"], invitation["id"]))
        if cur.rowcount != 1:
            # Belt and braces behind the row lock: if this ever matches nothing,
            # somebody redeemed it in between and this tenant must not exist.
            raise InvitationError(REJECTED)

    tenant_id = str(tenant["id"])
    key = issue_api_key(db, tenant_id, "first key", [])
    programs, pending = publish_starter_programs(db, tenant_id, blueprint)
    return {"tenant": {"id": tenant_id, "slug": tenant["slug"], "name": tenant["name"],
                       "region": tenant["region"], "blueprint": blueprint},
            "api_key": key.token,
            "programs": programs,
            # Seven of the eleven blueprints ship no example program yet. A
            # signup under one of those returns an empty list, and an empty
            # list with no sentence beside it is how a new tenant concludes the
            # product is broken.
            "programs_note": _starter_note(blueprint, programs, pending),
            "next": [
                "review each program and activate the ones you want running: "
                "POST /v1/programs/{id}/activate",
                "connect your CRM: POST /v1/crm/mappings, or use a built-in provider",
                "open the console at /console and sign in with the key above",
            ],
            "note": "the API key is shown once and is not recoverable"}


def _starter_note(blueprint: str, published: list[dict], pending: list[str]) -> str:
    if published:
        return (f"{len(published)} published as drafts. Nothing is running until you "
                "activate it: a tenant that starts contacting people before anyone "
                "has read a program is not onboarding, it is an incident.")
    if pending:
        return (f"no starter program ships for '{blueprint}' yet. The blueprint "
                f"declares {', '.join(sorted(pending))}; author them as YAML and "
                "publish with POST /v1/programs.")
    return (f"no starter program ships for '{blueprint}', and the blueprint declares "
            "none. Author your own and publish with POST /v1/programs.")


def _assert_known_blueprint(key: str) -> None:
    from zolts.blueprint import load_blueprints

    known = {b.key for b in load_blueprints()}
    if key not in known:
        # Named rather than silently defaulted: a tenant running the wrong
        # blueprint gets the wrong policy, and finds out by sending something.
        raise InvitationError(
            f"unknown blueprint '{key}'; known: {', '.join(sorted(known))}")


def publish_starter_programs(db: Database, tenant_id: str,
                             blueprint: str) -> tuple[list[dict], list[str]]:
    """Publish the example programs this blueprint claims, in draft.

    Draft rather than active. A tenant that signs up and starts contacting
    people before anyone has read a program is not onboarding, it is an
    incident.

    Returns what was published and what the blueprint promises but no file
    implements, so an empty result can say why it is empty.
    """
    from runtime.repo import programs as programs_repo
    from zolts.catalog import load_catalog

    catalog = load_catalog()
    published = []
    for program in catalog.programs:
        declared = program.raw.get("metadata", {}).get("blueprint")
        if declared and declared != blueprint:
            continue
        with db.tenant_tx(tenant_id) as cur:
            row = programs_repo.publish(
                cur, tenant_id, key=program.key, version=program.version,
                spec=program.spec, spec_hash=program.spec_hash, status="draft",
                created_by="signup", metadata=program.raw["metadata"])
        published.append({"id": str(row["id"]), "key": program.key,
                          "version": program.version, "status": "draft"})

    declared_by_blueprint = catalog.blueprints.get(blueprint)
    promised = set(declared_by_blueprint.programs) if declared_by_blueprint else set()
    pending = sorted(promised - {p["key"] for p in published})
    return published, pending
