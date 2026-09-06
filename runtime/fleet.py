"""The sending fleet, as the database holds it.

`zolts.deliverability` decides what a mailbox may send; this decides what the
fleet *is*. The split is the same one billing uses: the reference core does the
arithmetic, the runtime supplies the facts and does the I/O.

Every metric here is derived from the touches a mailbox actually sent. None of
them is stored. That is deliberate and it is the whole reason this module reads
the way it does: the `mailbox` table shipped with `sent_30d`, `bounce_rate`,
`complaint_rate` and `reply_rate` columns, and in the time it took to write the
rest of the runtime not one line was ever written to them. A rate you store is
correct at the moment you write it and wrong from then on, and the failure is
silent — a stale 0.0% bounce rate looks exactly like a healthy mailbox.

**A tenant with no registered domain is not gated.** Sending goes through a
provider that owns its own mailboxes, and this runtime cannot enforce capacity
on mailboxes it has never been told about. Refusing to send in that state would
block a tenant for a reason no operator could act on. So the absence is
reported instead of enforced, everywhere it matters, because believing you have
a control you do not have is worse than knowing you have none.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from runtime.db import one
from zolts.deliverability import Domain, Fleet, Mailbox, Metrics, Provider

# The window reputation is scored over. Providers weigh recent behaviour, and a
# mailbox that bounced hard three months ago and has been clean since is not
# the mailbox its lifetime average describes.
WINDOW_DAYS = 30

# Which recipient domains belong to which provider. Deliberately small: a
# wrong guess segregates traffic incorrectly, and `other` is the honest answer
# for anything not recognised.
_GOOGLE = {"gmail.com", "googlemail.com"}
_MICROSOFT = {"outlook.com", "hotmail.com", "live.com", "msn.com"}


def provider_for(email: str | None) -> Provider:
    """The recipient's mail provider, from their address.

    Only the consumer domains are recognised by name. A company on Google
    Workspace or Microsoft 365 sends from its own domain and cannot be told
    apart without an MX lookup, which is a network call in a code path that
    must not make one. `other` is the honest answer, and it means such traffic
    is segregated together rather than segregated wrongly.
    """
    if not email or "@" not in email:
        return Provider.OTHER
    host = email.rsplit("@", 1)[1].strip().lower()
    if host in _GOOGLE:
        return Provider.GOOGLE
    if host in _MICROSOFT:
        return Provider.MICROSOFT
    return Provider.OTHER


def _warmup_day(started: date | None, today: date | None = None) -> int:
    """Which day of warm-up a mailbox is on.

    Day 1 is the day it started. A mailbox with no start date is treated as
    starting today rather than as fully warmed: the cautious reading of missing
    information is the one that sends less.
    """
    if started is None:
        return 1
    return max(1, ((today or date.today()) - started).days + 1)


def _metrics(row: dict[str, Any]) -> Metrics:
    return Metrics(
        sent=int(row.get("sent") or 0),
        bounced=int(row.get("bounced") or 0),
        complained=int(row.get("complained") or 0),
        replied=int(row.get("replied") or 0),
        unsubscribed=int(row.get("unsubscribed") or 0),
    )


def load(cur, *, now: datetime | None = None) -> Fleet:
    """The tenant's fleet, with every rate derived from its own sends.

    One query per mailbox would be one query per mailbox; this is a single
    aggregate over the reputation window, joined to the mailboxes.
    """
    moment = now or datetime.now(timezone.utc)
    since = moment - timedelta(days=WINDOW_DAYS)
    today = moment.astimezone(timezone.utc).date()
    midnight = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)

    cur.execute(
        "select m.id, m.address, m.domain, m.provider, m.paused,"
        "       m.warmup_started_on,"
        # `sent` counts touches that left the building, whatever became of them
        # afterwards: a bounced touch was still a send, and dividing bounces by
        # anything less than that overstates the rate.
        "       count(*) filter (where t.sent_at is not null and t.sent_at >= %s) as sent,"
        "       count(*) filter (where t.status = 'bounced' and t.sent_at >= %s) as bounced,"
        "       count(*) filter (where t.complained_at is not null"
        "                          and t.complained_at >= %s) as complained,"
        "       count(*) filter (where t.status = 'replied' and t.sent_at >= %s) as replied,"
        "       count(*) filter (where t.unsubscribed_at is not null"
        "                          and t.unsubscribed_at >= %s) as unsubscribed,"
        "       count(*) filter (where t.sent_at >= %s) as sent_today"
        "  from mailbox m left join touch t on t.mailbox_id = m.id"
        " group by m.id, m.address, m.domain, m.provider, m.paused, m.warmup_started_on"
        " order by m.domain, m.address",
        (since, since, since, since, since, midnight))
    rows = cur.fetchall()

    cur.execute("select * from sending_domain")
    domains = {row["name"]: row for row in cur.fetchall()}

    by_domain: dict[str, list[Mailbox]] = {}
    for row in rows:
        # A mailbox an operator paused by hand is out of the fleet entirely,
        # not merely at zero capacity: `select` skips it, and its metrics do
        # not dilute the domain's.
        if row["paused"]:
            continue
        by_domain.setdefault(row["domain"], []).append(Mailbox(
            address=str(row["address"]),
            provider=Provider(row["provider"]),
            warmup_day=_warmup_day(row["warmup_started_on"], today),
            metrics=_metrics(row),
            sent_today=int(row["sent_today"] or 0)))

    fleet = Fleet()
    for name, mailboxes in by_domain.items():
        registered = domains.get(name)
        if registered is None:
            # A mailbox on a domain nobody registered has no authentication
            # anybody has confirmed, so it gets the defaults an unverified
            # domain gets, which is no capacity.
            registered = {"spf": False, "dkim": False, "dmarc_policy": "none",
                          "one_click_unsubscribe": False, "paused": True,
                          "paused_reason": "domain is not registered"}
        if registered["paused"]:
            continue
        fleet.domains.append(Domain(
            name=name, mailboxes=mailboxes,
            spf=bool(registered["spf"]), dkim=bool(registered["dkim"]),
            dmarc_policy=str(registered["dmarc_policy"]),
            one_click_unsubscribe=bool(registered["one_click_unsubscribe"])))
    return fleet


def managed(cur) -> bool:
    """Whether this tenant's sending capacity is under our control at all.

    False means the provider owns the mailboxes and nothing here can stop a
    send. Callers surface it; they must not silently treat it as healthy.
    """
    cur.execute("select exists (select 1 from sending_domain) as any")
    return bool(cur.fetchone()["any"])


@dataclass(frozen=True)
class Allocation:
    """A mailbox to send from, or the reason there is none.

    "Allocated" rather than "used", and the distinction is not pedantry. A
    provider that owns its own mailboxes — Smartlead does — takes a recipient
    and picks the sender itself. On that path this is Zolts' intent and a bound
    on the volume it releases, not an observation of what sent.
    """
    mailbox_id: str | None
    address: str | None
    reason: str | None = None
    unmanaged: bool = False

    @property
    def granted(self) -> bool:
        return self.unmanaged or self.mailbox_id is not None


def allocate(cur, *, recipient: str | None, now: datetime | None = None) -> Allocation:
    """Pick a mailbox for this recipient, or say why the fleet cannot.

    The refusal is the point. `Fleet.select` returning nothing is a hard stop —
    sending past capacity is how a domain is burned, and a domain takes months
    to replace and cannot be bought.
    """
    if not managed(cur):
        return Allocation(mailbox_id=None, address=None, unmanaged=True,
                          reason="no sending domain is registered, so capacity "
                                 "is whatever the provider allows")

    fleet = load(cur, now=now)
    provider = provider_for(recipient)
    chosen = fleet.select(provider)
    if chosen is None:
        return Allocation(
            mailbox_id=None, address=None,
            reason=(f"no capacity for {provider.value} recipients: "
                    f"{fleet.remaining} sends left across the fleet"))

    cur.execute("select id from mailbox where address = %s", (chosen.address,))
    row = one(cur)
    if row is None:  # pragma: no cover - the fleet was built from this table
        return Allocation(mailbox_id=None, address=None,
                          reason=f"mailbox {chosen.address} disappeared mid-send")
    return Allocation(mailbox_id=str(row["id"]), address=chosen.address)


def health(cur, *, now: datetime | None = None) -> dict[str, Any]:
    """What an operator needs to see: per domain, per mailbox, and the verdict.

    Reports the unmanaged case as its own state rather than as a healthy fleet
    with no members, because those look identical in a summary and are opposite
    in consequence.
    """
    if not managed(cur):
        return {"managed": False, "capacity": 0, "remaining": 0, "domains": [],
                "note": "No sending domain is registered. Capacity is whatever "
                        "the sending provider allows, and this runtime cannot "
                        "stop a send or pause a burning domain."}

    fleet = load(cur, now=now)
    cur.execute("select name, paused, paused_reason from sending_domain"
                " where paused order by name")
    paused = [{"name": r["name"], "pausedReason": r["paused_reason"]}
              for r in cur.fetchall()]

    return {
        "managed": True,
        "capacity": fleet.capacity,
        "remaining": fleet.remaining,
        "domains": [{
            "name": domain.name,
            "capacity": domain.capacity,
            "health": domain.verdict.health.value,
            "rule": domain.verdict.rule_key,
            "rationale": domain.verdict.rationale,
            "authenticationIssues": domain.authentication_issues(),
            "mailboxes": [{
                "address": box.address,
                "provider": box.provider.value,
                "warmupDay": box.warmup_day,
                "capacity": box.capacity,
                "remaining": box.remaining,
                "sentToday": box.sent_today,
                "health": box.verdict.health.value,
                "rule": box.verdict.rule_key,
            } for box in domain.mailboxes],
        } for domain in fleet.domains],
        "pausedDomains": paused,
    }
