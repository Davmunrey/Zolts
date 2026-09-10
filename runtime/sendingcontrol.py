"""The operator's hand on the sending switch.

`zolts/sendingcontrol` holds the rules. This holds the two acts and what they
write down, and it is deliberately narrow: one statement each, against one
column, with nothing else in the same breath.

That narrowness is the point of the module existing. The only way to lift a
pause before this was `zolts domain --resume`, which is a flag on the command
that *registers* a domain, so the update it performs is an upsert of the
domain's SPF, DKIM, DMARC and unsubscribe state. An operator lifting a pause
under time pressure had to restate four authentication facts correctly or
silently rewrite them, and the failure that produces does not raise: it arrives
weeks later as mail filtered on delivery.

**The rate that decides a resume counts every mailbox on the domain, including
ones an operator has since paused.** `fleet.load` deliberately drops a paused
mailbox so its metrics do not dilute the rest of the fleet, which is right for
capacity and wrong here: a domain that tripped the complaint cut-off would read
as recovered the moment somebody paused the mailbox that did it, and the
console would call the most dangerous resume in the product an ordinary one.
The sends that burned the domain are the domain's sends.

**Audit, not policy decision.** Invariant 3 binds external actions, and this is
not one: nothing leaves the building. It is an operator changing what the
runtime is permitted to do, and the record that answers *who stopped sending
and why* is the audit log, which already has a screen. Writing it as a policy
decision would need a rule key and a pack digest that name nothing, and would
put a row in the Policy view that is not a policy decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from runtime.db import one
from runtime.fleet import WINDOW_DAYS
from runtime.repo import ledger
from zolts.deliverability import Metrics, Verdict, assess
from zolts.sendingcontrol import Act, Refusal, Resumption, judge_resume, refuse_reason


@dataclass(frozen=True)
class Outcome:
    """What happened, or precisely what did not.

    A refusal is never a silent no-op: an operator who pressed Stop and was
    told nothing believes the sending stopped.
    """

    act: Act
    domain: str
    refusal: Refusal | None = None
    resumption: Resumption | None = None
    verdict: Verdict | None = None

    @property
    def done(self) -> bool:
        return self.refusal is None


def domain_metrics(cur, name: str, *, now: datetime | None = None) -> Metrics:
    """The domain's rates over the reputation window, across every mailbox.

    Paused mailboxes included, for the reason in the module docstring: dropping
    them would let pausing a mailbox launder the history that tripped the
    cut-off.
    """
    since = (now or datetime.now(timezone.utc)) - timedelta(days=WINDOW_DAYS)
    cur.execute(
        "select count(*) filter (where t.sent_at is not null"
        "                          and t.sent_at >= %s) as sent,"
        "       count(*) filter (where t.status = 'bounced'"
        "                          and t.sent_at >= %s) as bounced,"
        "       count(*) filter (where t.complained_at is not null"
        "                          and t.complained_at >= %s) as complained,"
        "       count(*) filter (where t.status = 'replied'"
        "                          and t.sent_at >= %s) as replied,"
        "       count(*) filter (where t.unsubscribed_at is not null"
        "                          and t.unsubscribed_at >= %s) as unsubscribed"
        "  from mailbox m left join touch t on t.mailbox_id = m.id"
        " where m.domain = %s",
        (since, since, since, since, since, name))
    row = one(cur) or {}
    # Built here rather than through `fleet._metrics`: a module reaching across
    # for another's private helper is a dependency nothing declares and nothing
    # checks, and this one would break by rename with a green suite.
    return Metrics(
        sent=int(row.get("sent") or 0),
        bounced=int(row.get("bounced") or 0),
        complained=int(row.get("complained") or 0),
        replied=int(row.get("replied") or 0),
        unsubscribed=int(row.get("unsubscribed") or 0))


def _domain(cur, name: str) -> dict[str, Any] | None:
    cur.execute("select * from sending_domain where name = %s", (name,))
    return one(cur)


def pause(cur, tenant_id: str, name: str, *, reason: str | None,
          actor: str) -> Outcome:
    """Stop a domain sending, now, because a person said so.

    Nothing in this repository could do this. `runtime/breakers.py` was the
    only writer of the column, so the only actor able to stop a send was a
    cut-off firing on rates that had already been earned. An operator who knew
    before the numbers did — a list bought rather than built, a misaddressed
    campaign, a partner on the phone — could only watch.
    """
    refusal = refuse_reason(reason)
    if refusal is not None:
        return Outcome(Act.PAUSE, name, refusal=refusal)
    row = _domain(cur, name)
    if row is None:
        return Outcome(Act.PAUSE, name, refusal=Refusal.NOT_REGISTERED)
    if row["paused"]:
        return Outcome(Act.PAUSE, name, refusal=Refusal.ALREADY_PAUSED)

    text = (reason or "").strip()
    cur.execute(
        "update sending_domain set paused = true, paused_reason = %s,"
        " paused_at = now() where id = %s", (text, row["id"]))
    ledger.audit(cur, tenant_id, actor=actor, action="sending.domain.paused",
                 subject=name, detail={"reason": text, "by": "operator"})
    return Outcome(Act.PAUSE, name)


def resume(cur, tenant_id: str, name: str, *, reason: str | None, actor: str,
           now: datetime | None = None) -> Outcome:
    """Let a domain send again, and record which kind of act that was.

    The rates are read first and the verdict is written down with the reason,
    so an audit row for a resume says whether the person was agreeing with the
    measurement or overriding it. A log that records only that somebody lifted
    a cut-off is the log that gets read during the next incident by somebody
    who needs exactly the half it left out.
    """
    refusal = refuse_reason(reason)
    if refusal is not None:
        return Outcome(Act.RESUME, name, refusal=refusal)
    row = _domain(cur, name)
    if row is None:
        return Outcome(Act.RESUME, name, refusal=Refusal.NOT_REGISTERED)
    if not row["paused"]:
        return Outcome(Act.RESUME, name, refusal=Refusal.ALREADY_SENDING)

    verdict = assess(domain_metrics(cur, name, now=now))
    resumption = judge_resume(verdict)
    text = (reason or "").strip()
    cur.execute(
        "update sending_domain set paused = false, paused_reason = null,"
        " paused_at = null where id = %s", (row["id"],))
    ledger.audit(cur, tenant_id, actor=actor, action="sending.domain.resumed",
                 subject=name,
                 detail={"reason": text, "resumption": resumption.value,
                         "rule": verdict.rule_key,
                         "rationale": verdict.rationale,
                         "liftedReason": row["paused_reason"]})
    return Outcome(Act.RESUME, name, resumption=resumption, verdict=verdict)
