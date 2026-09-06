"""The cut-offs, and what each one stops.

`docs/09` assigns the two hard thresholds to different units, and the choice is
not arbitrary:

| Signal | Cut-off | What is paused |
|---|---|---|
| Spam complaints | 0.3% | The **domain**, including its healthy mailboxes |
| Bounces | 3% | The **program** |

A complaint rate is a reputation problem and reputation is scored per domain,
so letting a clean mailbox keep sending from a burned domain is not a way out;
it is how the rest of the fleet follows. A bounce rate is a list-quality
problem: the addresses the program is targeting do not exist, and pausing the
domain would punish the infrastructure for the program's targeting.

Both are checked after a reputation event rather than on a schedule. A cut-off
evaluated nightly is a cut-off that lets a bad afternoon run to completion,
and the whole point of these numbers is that the damage is not recoverable on
the timescale that matters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from runtime import fleet
from zolts.deliverability import MIN_SAMPLE, Health

BOUNCE_CUTOFF = 0.03  # per docs/09; the program's, not the domain's


@dataclass(frozen=True)
class Tripped:
    """What a cut-off stopped, and why. Empty is the ordinary case."""
    domains: tuple[str, ...] = ()
    programs: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    @property
    def any(self) -> bool:
        return bool(self.domains or self.programs)


def _pause_domain(cur, name: str, reason: str) -> bool:
    cur.execute(
        "update sending_domain set paused = true, paused_reason = %s,"
        " paused_at = now() where name = %s and not paused",
        (reason, name))
    return cur.rowcount > 0


def check(cur, *, mailbox_id: str | None, program_id: str | None,
          now: datetime | None = None) -> Tripped:
    """Re-assess after a reputation event and pause whatever crossed a cut-off.

    Idempotent: pausing what is already paused changes nothing and reports
    nothing, so a provider that redelivers an event does not produce a second
    incident.
    """
    moment = now or datetime.now(timezone.utc)
    domains: list[str] = []
    programs: list[str] = []
    reasons: list[str] = []

    if mailbox_id:
        cur.execute("select domain from mailbox where id = %s", (mailbox_id,))
        row = cur.fetchone()
        if row is not None:
            name = row["domain"]
            current = fleet.load(cur, now=moment)
            for domain in current.domains:
                if domain.name != name:
                    continue
                verdict = domain.verdict
                # Only a complaint cut-off pauses the domain. A domain whose
                # bounce rate crossed 3% has a program pointing at addresses
                # that do not exist, and pausing the infrastructure would stop
                # every other program that shares it for a fault none of them
                # has.
                if (verdict.health is Health.PAUSED
                        and verdict.rule_key == "complaint.cutoff"):
                    if _pause_domain(cur, name, verdict.rationale):
                        domains.append(name)
                        reasons.append(f"{name}: {verdict.rationale}")

    if program_id:
        cur.execute(
            "select count(*) filter (where t.sent_at is not null) as sent,"
            "       count(*) filter (where t.status = 'bounced') as bounced"
            "  from touch t join enrollment e on e.id = t.enrollment_id"
            " where e.program_id = %s and t.sent_at >= %s",
            (program_id, moment - timedelta(days=fleet.WINDOW_DAYS)))
        row = cur.fetchone()
        sent = int(row["sent"] or 0)
        bounced = int(row["bounced"] or 0)
        # The same sample floor the mailbox thresholds use. One bounce in three
        # sends is 33% and pausing a program on it would make every new
        # program unusable on its first morning.
        if sent >= MIN_SAMPLE and bounced / sent >= BOUNCE_CUTOFF:
            cur.execute(
                "update program set status = 'paused' where id = %s and status = 'live'",
                (program_id,))
            if cur.rowcount > 0:
                programs.append(program_id)
                reasons.append(
                    f"program bounced at {bounced / sent:.1%} over {sent} sends, "
                    f"at or above the {BOUNCE_CUTOFF:.0%} cut-off")

    return Tripped(tuple(domains), tuple(programs), tuple(reasons))
