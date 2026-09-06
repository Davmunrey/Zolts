"""Sending capacity as managed inventory.

Claim under test (docs/09): reputable sending capacity is the scarce resource
of modern GTM, so it is modelled as inventory rather than treated as plumbing.

This is the one specification in the repository whose errors do not surface as a
failing test. A miscalculated capacity, a threshold checked one comparison too
late, a mailbox left sending while its complaint rate climbs — none of it
raises. It shows up weeks later as a burned domain and a customer whose mail
lands in spam, which is unrecoverable on the timescale that matters. That is
the argument for executing it before any real domain exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

BASE_CAP = 40  # emails per mailbox per day at full warm-up, per docs/09


class Health(str, Enum):
    OK = "ok"
    ALARM = "alarm"
    PAUSED = "paused"


class Provider(str, Enum):
    """Recipient mail provider. Traffic is segregated by provider because
    reputation is scored per provider, so mixing them on one mailbox makes a
    reputation problem at one provider invisible until it affects both."""
    GOOGLE = "google"
    MICROSOFT = "microsoft"
    OTHER = "other"


@dataclass(frozen=True)
class Verdict:
    health: Health
    rule_key: str
    rationale: str

    @property
    def sending(self) -> bool:
        return self.health is not Health.PAUSED


@dataclass
class Metrics:
    """Rolling rates over the reputation window."""
    sent: int = 0
    bounced: int = 0
    complained: int = 0
    replied: int = 0
    unsubscribed: int = 0

    def _rate(self, count: int) -> float:
        return count / self.sent if self.sent else 0.0

    @property
    def bounce_rate(self) -> float:
        return self._rate(self.bounced)

    @property
    def complaint_rate(self) -> float:
        return self._rate(self.complained)

    @property
    def reply_rate(self) -> float:
        return self._rate(self.replied)

    @property
    def unsubscribe_rate(self) -> float:
        return self._rate(self.unsubscribed)


# The table in docs/09, as data. Ordered worst-first: the first rule that
# matches decides, so a cut-off is never masked by an alarm on another metric.
THRESHOLDS = (
    ("complaint.cutoff",   "complaint_rate",   0.003,  Health.PAUSED,
     "spam complaints at or above 0.3%, the major providers' hard threshold"),
    ("bounce.cutoff",      "bounce_rate",      0.03,   Health.PAUSED,
     "bounce rate at or above 3%"),
    ("unsubscribe.review", "unsubscribe_rate", 0.02,   Health.ALARM,
     "unsubscribe rate at or above 2%"),
    ("complaint.alarm",    "complaint_rate",   0.001,  Health.ALARM,
     "spam complaints at or above 0.1%"),
    ("bounce.alarm",       "bounce_rate",      0.02,   Health.ALARM,
     "bounce rate at or above 2%"),
    ("unsubscribe.alarm",  "unsubscribe_rate", 0.01,   Health.ALARM,
     "unsubscribe rate at or above 1%"),
)

# Minimum volume before a rate means anything. One bounce out of three sends is
# 33% and tells you nothing; pausing on it would make every new mailbox
# unusable on its first day.
MIN_SAMPLE = 50


def assess(metrics: Metrics) -> Verdict:
    """Health of a mailbox or domain. Every verdict carries a rule key, for the
    same reason policy decisions do: an unexplained pause gets worked around."""
    if metrics.sent < MIN_SAMPLE:
        return Verdict(Health.OK, "sample.insufficient",
                       f"{metrics.sent} sends is below the {MIN_SAMPLE} a rate needs")

    for rule_key, attr, limit, health, text in THRESHOLDS:
        if getattr(metrics, attr) >= limit:
            return Verdict(health, rule_key,
                           f"{text} (observed {getattr(metrics, attr):.3%})")

    # A collapsed reply rate is a targeting failure, not a delivery one, but it
    # precedes a reputation failure reliably enough to be worth catching here.
    if metrics.reply_rate < 0.01:
        return Verdict(Health.ALARM, "reply.collapsed",
                       f"reply rate {metrics.reply_rate:.2%} is below 1%; "
                       "segment and copy need review")

    return Verdict(Health.OK, "ok", "all thresholds clear")


def warmup_factor(day: int) -> float:
    """Share of base capacity a mailbox may use on a given day of warm-up.

    Six weeks from 10% to full, per docs/09. Linear rather than clever: the
    curve's shape matters far less than the fact that day one is not day forty.
    """
    if day < 1:
        return 0.0
    if day >= 42:
        return 1.0
    return round(0.1 + 0.9 * (day - 1) / 41, 4)


def reputation_factor(metrics: Metrics) -> float:
    """Multiplier in [0, 1.2]. A clean, engaged mailbox earns above its base
    rate; a paused one earns nothing."""
    verdict = assess(metrics)
    if verdict.health is Health.PAUSED:
        return 0.0
    if verdict.health is Health.ALARM:
        return 0.5
    if metrics.sent < MIN_SAMPLE:
        return 1.0
    # Engagement is what providers actually reward, so it is what earns headroom.
    bonus = 0.2 if metrics.reply_rate >= 0.04 and metrics.complaint_rate < 0.0005 else 0.0
    return 1.0 + bonus


@dataclass
class Mailbox:
    address: str
    provider: Provider
    warmup_day: int = 1
    metrics: Metrics = field(default_factory=Metrics)
    sent_today: int = 0

    @property
    def verdict(self) -> Verdict:
        return assess(self.metrics)

    @property
    def capacity(self) -> int:
        return int(BASE_CAP * warmup_factor(self.warmup_day) * reputation_factor(self.metrics))

    @property
    def remaining(self) -> int:
        return max(0, self.capacity - self.sent_today)

    def demote(self) -> None:
        """A mailbox whose reputation degrades goes back into warm-up rather
        than merely slowing down. Halving the day is the recoverable version of
        starting over."""
        self.warmup_day = max(1, self.warmup_day // 2)


@dataclass
class Domain:
    name: str
    mailboxes: list[Mailbox] = field(default_factory=list)
    spf: bool = True
    dkim: bool = True
    dmarc_policy: str = "quarantine"
    one_click_unsubscribe: bool = True

    @property
    def metrics(self) -> Metrics:
        total = Metrics()
        for m in self.mailboxes:
            total.sent += m.metrics.sent
            total.bounced += m.metrics.bounced
            total.complained += m.metrics.complained
            total.replied += m.metrics.replied
            total.unsubscribed += m.metrics.unsubscribed
        return total

    @property
    def verdict(self) -> Verdict:
        return assess(self.metrics)

    def authentication_issues(self) -> list[str]:
        """Missing authentication is not a reputation problem to be recovered
        from — it is mail that gets filtered on arrival."""
        issues = []
        if not self.spf:
            issues.append("spf.missing")
        if not self.dkim:
            issues.append("dkim.missing")
        if self.dmarc_policy not in {"quarantine", "reject"}:
            issues.append("dmarc.too_weak")
        if not self.one_click_unsubscribe:
            issues.append("list_unsubscribe.missing")
        return issues

    @property
    def capacity(self) -> int:
        if self.authentication_issues() or not self.verdict.sending:
            return 0
        return sum(m.capacity for m in self.mailboxes if m.verdict.sending)


@dataclass
class Fleet:
    domains: list[Domain] = field(default_factory=list)

    @property
    def capacity(self) -> int:
        return sum(d.capacity for d in self.domains)

    @property
    def remaining(self) -> int:
        return sum(m.remaining for d in self.domains
                   if d.capacity and not d.authentication_issues()
                   for m in d.mailboxes if m.verdict.sending)

    def select(self, provider: Provider) -> Mailbox | None:
        """Pick a mailbox for a recipient on this provider.

        Segregated by provider first, then by most remaining headroom, so load
        spreads instead of exhausting the healthiest mailbox first. Returns None
        when the fleet has nothing left, which the caller must treat as a hard
        stop: sending past capacity is how a domain is burned.
        """
        candidates = [
            m for d in self.domains
            if d.capacity and not d.authentication_issues()
            for m in d.mailboxes
            if m.provider is provider and m.verdict.sending and m.remaining > 0
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda m: (m.remaining, m.address))


def ramp_plan(new_domains: int, per_wave: int = 2, wave_gap_days: int = 7) -> list[tuple[int, int]]:
    """Schedule for bringing domains online: (day, count) per wave.

    Activating a fleet at once is the mass-activation pattern docs/09 forbids —
    every domain then shares one reputation history, so a single mistake takes
    all of them down together instead of one.
    """
    if new_domains <= 0:
        return []
    waves, remaining, day = [], new_domains, 1
    while remaining > 0:
        waves.append((day, min(per_wave, remaining)))
        remaining -= per_wave
        day += wave_gap_days
    return waves
