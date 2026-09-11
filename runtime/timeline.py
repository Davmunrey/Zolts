"""The rows behind one contact, joined for the first time.

`zolts.timeline` says what a timeline is. This reads it out of the six tables
that hold it. Every query here is scoped by the tenant transaction the caller
opened, so a person id from another tenant reads as no person, not as a
neighbour's timeline.

**Account events belong to the person.** A signal or an enrolment on an account
is on this person's timeline when they are a member of that account, because
that is how the send reached them: the worker resolves an account enrolment to
a contact through the same membership table. Leaving account rows out would
show a contact who received three emails and nothing that explains one.

**Older touches are found through the enrolment.** `touch.person_id` exists
since ADR-056; a touch written before it names nobody. Those are recovered
through `enrollment_id`, so the timeline does not start on the day the column
did.
"""

from __future__ import annotations

from typing import Any

from runtime.db import one, rows
from runtime.repo import entities
from zolts.timeline import order


def _account_ids(cur, person_id: str) -> list[str]:
    cur.execute("select account_id from membership where person_id = %s",
                (person_id,))
    return [str(r["account_id"]) for r in cur.fetchall()]


def for_person(cur, person_id: str) -> dict[str, Any] | None:
    person = entities.get_person(cur, person_id)
    if person is None:
        return None
    accounts = _account_ids(cur, person_id)
    events: list[dict[str, Any]] = []

    # Signals on the person, and on any account they belong to.
    cur.execute(
        "select s.*, a.name as account_name from signal s"
        " left join account a on a.id = s.entity_id and s.entity_type = 'account'"
        " where (s.entity_type = 'person' and s.entity_id = %s)"
        "    or (s.entity_type = 'account' and s.entity_id = any(%s::uuid[]))"
        " order by s.observed_at desc limit 200",
        (person_id, accounts))
    for s in cur.fetchall():
        events.append({
            "kind": "signal.observed", "at": s["observed_at"].isoformat(),
            "title": s["type"],
            "detail": (f"on {s['account_name']}" if s["account_name"]
                       else "on this person"),
            "source": s["source"], "strength": float(s["strength"] or 0),
            "legalBasis": s["legal_basis"],
        })

    # Enrolments, on the person or on their accounts.
    cur.execute(
        "select e.*, p.key as program_key from enrollment e"
        " join program p on p.id = e.program_id"
        " where (e.entity_type = 'person' and e.entity_id = %s)"
        "    or (e.entity_type = 'account' and e.entity_id = any(%s::uuid[]))"
        " order by e.entered_at desc limit 100",
        (person_id, accounts))
    enrollments = rows(cur)
    enrollment_ids = [str(e["id"]) for e in enrollments]
    for e in enrollments:
        events.append({
            "kind": "enrollment.entered", "at": e["entered_at"].isoformat(),
            "title": e["program_key"],
            "detail": f"{e['variant']} arm, tier {e['tier'] or '—'}",
            "program": e["program_key"], "variant": e["variant"],
            "state": e["state"],
        })
        if e["exited_at"] is not None:
            events.append({
                "kind": "enrollment.exited", "at": e["exited_at"].isoformat(),
                "title": e["program_key"],
                "detail": e["exit_reason"] or "no reason recorded",
                "program": e["program_key"],
            })

    # Policy decisions about this person. The pack digest is the rule as it
    # was on the day, which is what makes the decision reproducible (D-53).
    cur.execute(
        "select * from policy_decision where subject_type = 'person'"
        " and subject_id = %s order by decided_at desc limit 200", (person_id,))
    for d in cur.fetchall():
        events.append({
            "kind": ("decision.allow" if d["decision"] == "allow"
                     else "decision.deny"),
            "at": d["decided_at"].isoformat(),
            "title": f"{d['action']}: {d['decision']}",
            "detail": d["rationale"],
            "rule": d["rule_key"], "jurisdiction": d["jurisdiction"],
            "packVersion": d["pack_version"], "packDigest": d["pack_digest"],
        })

    # Proposals, through the enrolment. Evidence and the claims removed for
    # having none travel with the row: the sentence beside the kind promises
    # them, and a promise the screen cannot show is a promise it should not
    # make.
    proposal_ids: list[str] = []
    if enrollment_ids:
        cur.execute(
            "select * from proposal where enrollment_id = any(%s::uuid[])"
            " order by created_at desc limit 100", (enrollment_ids,))
        for p in cur.fetchall():
            proposal_ids.append(str(p["id"]))
            content = p["content"] or {}
            events.append({
                "kind": "proposal.drafted", "at": p["created_at"].isoformat(),
                "title": f"{p['agent']} · {p['step_key']}",
                "detail": f"{p['state']}; {p['gate_reason'] or 'no gate reason'}",
                "evidence": p["evidence"] or [],
                "droppedClaims": content.get("dropped_claims") or [],
                "evalScore": (None if p["eval_score"] is None
                              else float(p["eval_score"])),
                "model": p["model"], "costEur": (p["cost_micros"] or 0) / 1e6,
                "state": p["state"], "approvedBy": p["approved_by"],
            })

    # Touches: by person since ADR-056, and by enrolment for rows before it.
    cur.execute(
        "select t.*, coalesce(t.person_id::text, '') as who from touch t"
        " where t.person_id = %s"
        "    or (t.person_id is null and t.enrollment_id = any(%s::uuid[]))"
        " order by coalesce(t.sent_at, t.created_at) desc limit 200",
        (person_id, enrollment_ids))
    for t in cur.fetchall():
        status = t["status"]
        key = ("touch.queued" if status == "queued"
               else "touch.failed" if status in ("failed", "bounced")
               else "touch.sent")
        at = t["sent_at"] or t["created_at"]
        events.append({
            "kind": key, "at": at.isoformat(),
            "title": f"{t['channel']} · {t['step_key'] or 'step'}",
            "detail": (f"via {t['provider']}" if t["provider"]
                       else "no provider" if key != "touch.queued"
                       else "awaiting a person"),
            "status": status, "provider": t["provider"],
            "costEur": (t["cost_micros"] or 0) / 1e6,
            "direction": t["direction"],
        })

    # Outcomes, through the enrolment.
    if enrollment_ids:
        cur.execute(
            "select * from outcome where enrollment_id = any(%s::uuid[])"
            " order by occurred_at desc limit 100", (enrollment_ids,))
        for o in cur.fetchall():
            events.append({
                "kind": "outcome.recorded", "at": o["occurred_at"].isoformat(),
                "title": o["type"],
                "detail": (f"{o['source']}" + (", verified" if o.get("verified_by")
                                                else ", unread")
                           if o["type"] == "reply_positive" else o["source"]),
                "valueEur": (o["value_micros"] or 0) / 1e6,
            })

    # Audit entries whose subject is this person or anything of theirs.
    subjects = [person_id, *enrollment_ids, *proposal_ids]
    cur.execute(
        "select * from audit_log where subject = any(%s::text[])"
        " order by at desc limit 200", (subjects,))
    for a in cur.fetchall():
        events.append({
            "kind": "audit.entry", "at": a["at"].isoformat(),
            "title": a["action"], "detail": a["actor"],
            "actor": a["actor"], "auditDetail": a["detail"] or {},
        })

    return {
        "person": {
            "id": str(person["id"]), "name": person["full_name"],
            "email": person["email"], "country": person["country"],
            "consent": person["consent_state"] or {},
        },
        "accounts": accounts,
        "events": order(events),
        "counts": {k: sum(1 for e in events if e["kind"] == k)
                   for k in sorted({e["kind"] for e in events})},
    }


def subject_request(cur, person_id: str) -> dict[str, Any] | None:
    """Everything the product holds about one person, in one document.

    The access and portability half of a subject access request (`docs/11`,
    COMP-2): the stored record, every account they belong to, the consent
    state as recorded, and the full timeline with the legal basis on every
    signal and the rule and pack digest on every decision. Erasure, the
    propagation to a sub-processor and the certificate are the other half and
    are still not built; the document says so rather than implying otherwise.

    Built on the timeline rather than beside it, so a DPO's export and an
    operator's screen cannot disagree about what happened.
    """
    story = for_person(cur, person_id)
    if story is None:
        return None
    cur.execute("select * from person where id = %s", (person_id,))
    person = dict(one(cur))
    cur.execute(
        "select a.name, a.domain, m.title, m.buying_role, m.started_at, m.ended_at"
        "  from membership m join account a on a.id = m.account_id"
        " where m.person_id = %s order by m.started_at", (person_id,))
    memberships = [{**dict(r), "started_at": r["started_at"].isoformat(),
                    "ended_at": r["ended_at"].isoformat() if r["ended_at"] else None}
                   for r in cur.fetchall()]
    return {
        "kind": "subject_request",
        "scope": "access and portability; erasure is not included",
        "person": {
            "id": str(person["id"]), "fullName": person["full_name"],
            "email": person["email"], "phone": person.get("phone"),
            "country": person["country"], "linkedinUrn": person["linkedin_urn"],
            "consentState": person["consent_state"] or {},
            "attributes": person["attributes"] or {},
            "crmId": person["crm_id"],
            "createdAt": person["created_at"].isoformat(),
        },
        "memberships": memberships,
        "timeline": story["events"],
        "counts": story["counts"],
    }
