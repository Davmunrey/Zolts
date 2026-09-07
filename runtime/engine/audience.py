"""Who a program is allowed to enrol.

`spec.audience` is required by the schema, validated on every publish, and
until now read by nothing. Enrolment matched on the signal type, the trigger
window, the trigger predicate, the cooldown and the holdout — and never on
membership. A program whose audience says

    where a.industry_code_group = 'software'
      and not exists (select 1 from opportunity o
                       where o.account_id = a.id and o.state = 'open')

enrolled anyone who emitted the trigger, including the accounts with a live
opportunity that the audience exists to exclude. The specification was
complete, the validation was real, and the field was decorative.

**It is a membership test, not a list.** The runtime asks "is this subject in
the audience", one subject at a time, because that is the question enrolment
has. Materialising the whole audience per signal would be the same answer at a
much larger cost, and the answer would be stale the moment it was computed.

**The subject column is part of the contract.** An audience selects
`account_id` or `person_id`; the runtime matches whichever it exposes against
the entity the signal carries. An audience that exposes neither cannot be
evaluated and is refused at publish rather than at three in the morning.

**It fails closed.** A broken audience — bad SQL, a table this tenant does not
have, a query slower than its timeout — stops the enrolment rather than
allowing it. The alternative is what shipped: contacting people the program
promised not to contact. A refusal is recorded, so a program that has silently
stopped enrolling is visible rather than merely quiet.
"""

from __future__ import annotations

import re
from typing import Any

import psycopg

# How long an audience may take to answer for one subject. It runs inside the
# enrolment transaction, so a slow audience does not merely delay itself: it
# holds the signal that triggered it. Bounded here rather than trusted.
STATEMENT_TIMEOUT_MS = 5_000

SUBJECT_COLUMNS = ("account_id", "person_id")

# Statements that write. The audience is the tenant's own SQL over the tenant's
# own rows, so this is not a privilege boundary — row level security is, and it
# holds regardless. This is the guard against a mistake: an audience that
# writes would be executed once per signal by the worker, and the damage would
# be discovered long after the publish that caused it.
_WRITES = re.compile(
    r"\b(insert|update|delete|merge|truncate|drop|alter|create|grant|revoke|copy)\b",
    re.IGNORECASE)


class AudienceError(ValueError):
    """An audience that cannot be evaluated. Enrolment refuses rather than allows."""


def declared(spec: dict[str, Any]) -> dict[str, Any]:
    return spec.get("audience") or {}


def check(spec: dict[str, Any], program_key: str) -> None:
    """Refuse an audience the runtime could not evaluate, at publish time.

    Every failure below becomes a program that enrols nobody once it is live.
    Finding that out at publish costs a 422; finding it out later costs a
    campaign that looks armed and does nothing.
    """
    block = declared(spec)
    if not block:
        raise AudienceError(f"program '{program_key}' declares no audience")

    if "segment_ref" in block and "sql" not in block:
        # The schema allows it and nothing resolves one. Refusing is the
        # honest answer: a program pointing at a segment that does not exist
        # would otherwise enrol nobody and say nothing. Decision 31.
        raise AudienceError(
            f"program '{program_key}' selects its audience by segment_ref "
            f"'{block['segment_ref']}', and segments are not implemented. "
            "Express the audience as SQL.")

    sql = block.get("sql")
    if not sql or not sql.strip():
        raise AudienceError(f"program '{program_key}' has an empty audience")

    if _WRITES.search(sql):
        raise AudienceError(
            f"program '{program_key}' has an audience that modifies data. "
            "An audience answers a question; it does not change the answer.")

    if not any(column in sql for column in SUBJECT_COLUMNS):
        raise AudienceError(
            f"program '{program_key}' has an audience that selects neither "
            f"{' nor '.join(SUBJECT_COLUMNS)}, so no subject can be matched against it")


def subject_column(sql: str) -> str:
    """Which identifier this audience yields.

    Read from the query rather than declared beside it, because a declaration
    beside the query is a second thing to keep true.
    """
    for column in SUBJECT_COLUMNS:
        if re.search(r"\bas\s+" + column + r"\b", sql, re.IGNORECASE) or \
           re.search(r"\b" + column + r"\b", sql, re.IGNORECASE):
            return column
    raise AudienceError("the audience selects no recognised subject column")


def includes(cur, spec: dict[str, Any], program_key: str, entity_id: str) -> bool:
    """Is this entity in the program's audience?

    Runs as the application role inside the caller's tenant transaction, so row
    level security confines it to this tenant exactly as it confines everything
    else. The timeout is set `local`, so it lasts the statement and not the
    connection.
    """
    check(spec, program_key)
    sql = declared(spec)["sql"]
    column = subject_column(sql)

    # A savepoint, because a failing statement aborts the whole transaction and
    # everything after it — including the audit row that records *why* the
    # enrolment was refused. Catching the exception without this leaves the
    # caller holding a transaction in which nothing further can run, so the
    # signal that triggered the enrolment would be lost too.
    try:
        with cur.connection.transaction():
            cur.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
            cur.execute(
                f"select 1 from ({sql}) as audience where audience.{column} = %s limit 1",
                (entity_id,))
            found = cur.fetchone() is not None
    except psycopg.errors.QueryCanceled as exc:
        raise AudienceError(
            f"program '{program_key}' has an audience that did not answer within "
            f"{STATEMENT_TIMEOUT_MS}ms") from exc
    except psycopg.Error as exc:
        # A table this tenant does not have, a column that was renamed, a typo
        # the schema cannot catch because the schema validates the document
        # rather than the query.
        raise AudienceError(
            f"program '{program_key}' has an audience that failed: "
            f"{str(exc).strip()[:200]}") from exc

    return found
