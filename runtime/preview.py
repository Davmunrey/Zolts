"""What activating a programme would do today, computed by the functions that
will do it.

`zolts.preview` holds the rules; this composes the same pieces enrolment uses
— the audience SQL, `experiment.assign`, the cooldown, the tier capacity — and
writes nothing. The one honest way to preview a runtime is to run its own
functions with the insert taken out, and a test in `tests/test_preview.py`
holds that equality: the preview for a programme equals what activating it
enrols in the same second.

**The audience is evaluated as a set, under the same guards as membership.**
`audience.includes` answers for one entity inside a savepoint with a statement
timeout, and refuses an audience that excludes by deals when no CRM has
delivered any. The set query here runs under the same savepoint, the same
timeout and the same refusal, so a preview cannot succeed on an audience that
enrolment would refuse.

**The policy sample is a sample and says so.** Evaluating every treatment
contact against the pack would make the preview as slow as the send. Fifty is
enough to name the rule that will refuse most of them, which is what an
operator needs before Activate: not the count, the reason.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

import psycopg

from runtime import policy_packs
from runtime.engine import audience, enroll, gate, triggers
from runtime.repo import enrollments, entities, ledger
from zolts import billing, experiment, policy
from zolts.preview import (Preview, credits_in_week_one, sends_in_week_one,
                           steps_in_week_one, unanswerable)

SAMPLE = 50


def members(cur, spec: dict[str, Any], program_key: str) -> list[str]:
    """Every subject the audience yields now, under membership's own guards."""
    audience.check(spec, program_key)
    sql = audience.declared(spec)["sql"]
    column = audience.subject_column(sql)
    if audience.relies_on_deals(sql) and not audience.deals_are_answerable(cur):
        raise audience.AudienceError(
            f"program '{program_key}' excludes accounts with an open deal, and no "
            "connected CRM has delivered any deals to exclude them by")
    try:
        with cur.connection.transaction():
            cur.execute(f"set local statement_timeout = {audience.STATEMENT_TIMEOUT_MS}")
            cur.execute(f"select audience.{column} as id from ({sql}) as audience")
            return [str(r["id"]) for r in cur.fetchall()]
    except psycopg.errors.QueryCanceled as exc:
        raise audience.AudienceError(
            f"program '{program_key}' has an audience that did not answer within "
            f"{audience.STATEMENT_TIMEOUT_MS}ms") from exc
    except psycopg.Error as exc:
        raise audience.AudienceError(
            f"program '{program_key}' has an audience that failed: "
            f"{str(exc).strip()[:200]}") from exc


def _first_contact(cur, spec: dict[str, Any], subject: str, column: str):
    if column == "person_id":
        return entities.get_person(cur, subject)
    roles = None
    plays = spec.get("plays") or {}
    tiers = (spec.get("route") or {}).get("tiers") or []
    if tiers:
        play = plays.get(tiers[0].get("key")) or {}
        for step in play.get("steps") or []:
            if step.get("buying_roles"):
                roles = step["buying_roles"]
                break
    found = entities.contacts_for_account(cur, subject, roles=roles, limit=1)
    return found[0] if found else None


def for_program(cur, tenant_id: str, program: dict[str, Any], *,
                now: datetime | None = None) -> dict[str, Any]:
    """The forecast, or the refusal, for one programme as of now."""
    moment = now or datetime.now(timezone.utc)
    as_of = moment.isoformat()
    spec = program["spec"]
    key = program["key"]

    try:
        subjects = members(cur, spec, key)
    except audience.AudienceError as exc:
        return unanswerable(as_of, str(exc))

    column = audience.subject_column(audience.declared(spec)["sql"])
    cooldown = triggers.cooldown_days(spec)
    if cooldown:
        subjects = [s for s in subjects
                    if not enrollments.in_cooldown(cur, str(program["id"]), s, cooldown)]

    pct = enroll.holdout_pct(spec, key)
    salt = (spec.get("experiment") or {}).get("salt", "")
    treatment_ids: list[str] = []
    control = 0
    for subject in subjects:
        if experiment.assign(subject, key, pct, salt).is_control:
            control += 1
        else:
            treatment_ids.append(subject)

    occupancy = enrollments.tier_counts_since(cur, str(program["id"]),
                                              enroll.CAPACITY_WINDOW_DAYS)
    tiers = (spec.get("route") or {}).get("tiers") or []
    headroom = {t["key"]: max(0, int(t.get("capacity_per_week") or 0)
                              - int(occupancy.get(t["key"], 0))) for t in tiers}

    top = tiers[0]["key"] if tiers else None
    play = (spec.get("plays") or {}).get(top) or {}
    steps = steps_in_week_one(play)
    treatment = len(treatment_ids)

    blocked: Counter[str] = Counter()
    sampled = 0
    first_send = next((s for s in steps if s.channel), None)
    if first_send and treatment_ids:
        try:
            pack_row = policy_packs.active(cur)
        except policy_packs.NoActivePack:
            pack_row = None
        if pack_row is not None:
            rules = policy_packs.rules_of(pack_row)
            overrides = (spec.get("policy") or {}).get("overrides") or {}
            cap = int(overrides.get("max_touches_per_person_per_week", 3))
            for subject in treatment_ids[:SAMPLE]:
                person = _first_contact(cur, spec, subject, column)
                if person is None:
                    continue
                sampled += 1
                contact = gate.build_contact(
                    cur, person, ledger.touches_this_week(cur, str(person["id"])))
                context = policy.ActionContext(
                    channel=first_send.channel, now=moment,
                    local_hour=gate.local_hour(contact.country, moment),
                    max_touches_per_week=cap)
                verdict = policy.evaluate(contact, context, pack=rules,
                                          overrides=overrides)
                if not verdict.allowed:
                    blocked[verdict.rule_key] += 1

    return Preview(
        as_of=as_of, audience=len(subjects), control=control, treatment=treatment,
        headroom=headroom, steps=steps,
        sends_at_most=sends_in_week_one(steps, treatment),
        credits_at_most=credits_in_week_one(steps, treatment, billing.CREDITS),
        blocked_by_rule=dict(blocked), sampled=sampled,
    ).as_dict() | {"tier": top}
