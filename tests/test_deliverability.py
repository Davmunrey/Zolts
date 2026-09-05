"""Sending capacity as managed inventory.

This is the one module whose errors do not surface as a failing test in
production. A miscalculated capacity or a threshold checked one comparison too
late shows up weeks later as a burned domain, which is unrecoverable on the
timescale that matters — so it is executed here, before a real domain exists.
"""

import pytest

from zolts.deliverability import (
    BASE_CAP,
    Domain,
    Fleet,
    Health,
    Mailbox,
    Metrics,
    Provider,
    assess,
    ramp_plan,
    reputation_factor,
    warmup_factor,
)


def mailbox(day=42, provider=Provider.GOOGLE, address="a@get.zolts.dev", **metrics):
    return Mailbox(address=address, provider=provider, warmup_day=day,
                   metrics=Metrics(**metrics))


# ── warm-up ──────────────────────────────────────────────────────────────

def test_a_new_mailbox_cannot_send_at_full_rate():
    """The failure this curve exists to prevent: a fresh domain sending like an
    established one, which is the fastest way to be classified as bulk."""
    assert warmup_factor(1) == pytest.approx(0.1)
    assert mailbox(day=1).capacity < BASE_CAP * 0.2


def test_warmup_is_monotonic_and_reaches_full_capacity():
    factors = [warmup_factor(d) for d in range(1, 45)]
    assert factors == sorted(factors)
    assert warmup_factor(42) == 1.0
    assert warmup_factor(100) == 1.0


def test_day_zero_sends_nothing():
    assert warmup_factor(0) == 0.0
    assert mailbox(day=0).capacity == 0


# ── thresholds ───────────────────────────────────────────────────────────

def test_a_rate_needs_a_sample_before_it_means_anything():
    """One bounce in three sends is 33%. Pausing on it would make every new
    mailbox unusable on its first day."""
    verdict = assess(Metrics(sent=3, bounced=1))
    assert verdict.health is Health.OK
    assert verdict.rule_key == "sample.insufficient"


def test_the_complaint_cutoff_pauses_sending():
    verdict = assess(Metrics(sent=1000, complained=3, replied=50))
    assert verdict.health is Health.PAUSED
    assert verdict.rule_key == "complaint.cutoff"
    assert not verdict.sending


def test_the_bounce_cutoff_pauses_sending():
    verdict = assess(Metrics(sent=1000, bounced=30, replied=50))
    assert verdict.health is Health.PAUSED
    assert verdict.rule_key == "bounce.cutoff"


def test_a_cutoff_is_never_masked_by_an_alarm_on_another_metric():
    """Rules are ordered worst-first for this reason: a mailbox both bouncing
    at 2% and complaining at 0.3% must pause, not merely alarm."""
    verdict = assess(Metrics(sent=1000, bounced=20, complained=3, replied=50))
    assert verdict.health is Health.PAUSED


@pytest.mark.parametrize("metrics,rule", [
    (Metrics(sent=1000, complained=1, replied=50), "complaint.alarm"),
    (Metrics(sent=1000, bounced=20, replied=50), "bounce.alarm"),
    (Metrics(sent=1000, unsubscribed=10, replied=50), "unsubscribe.alarm"),
    (Metrics(sent=1000, unsubscribed=20, replied=50), "unsubscribe.review"),
])
def test_alarms_fire_at_the_documented_thresholds(metrics, rule):
    verdict = assess(metrics)
    assert verdict.health is Health.ALARM
    assert verdict.rule_key == rule


def test_a_collapsed_reply_rate_alarms_before_reputation_does():
    """Targeting fails before delivery does, reliably enough to catch here."""
    verdict = assess(Metrics(sent=1000, replied=5))
    assert verdict.rule_key == "reply.collapsed"


def test_every_verdict_carries_a_rule_key_and_rationale():
    for m in (Metrics(sent=10), Metrics(sent=1000, replied=50),
              Metrics(sent=1000, complained=5)):
        v = assess(m)
        assert v.rule_key and v.rationale


# ── reputation ───────────────────────────────────────────────────────────

def test_engagement_earns_headroom_above_the_base_rate():
    clean = Metrics(sent=1000, bounced=5, complained=0, replied=60)
    assert reputation_factor(clean) == pytest.approx(1.2)
    assert mailbox(**{"sent": 1000, "bounced": 5, "replied": 60}).capacity > BASE_CAP


def test_a_paused_mailbox_has_no_capacity():
    assert mailbox(sent=1000, complained=5, replied=50).capacity == 0


def test_an_alarmed_mailbox_is_halved_not_stopped():
    assert reputation_factor(Metrics(sent=1000, bounced=20, replied=50)) == 0.5


def test_a_degraded_mailbox_returns_to_warmup():
    """docs/09: a mailbox whose reputation degrades is pulled back into
    warm-up, not merely slowed."""
    box = mailbox(day=40)
    before = box.capacity
    box.demote()
    assert box.warmup_day == 20
    assert box.capacity < before


def test_demotion_never_falls_below_day_one():
    box = mailbox(day=1)
    box.demote()
    assert box.warmup_day == 1


# ── authentication ───────────────────────────────────────────────────────

@pytest.mark.parametrize("kwargs,issue", [
    ({"spf": False}, "spf.missing"),
    ({"dkim": False}, "dkim.missing"),
    ({"dmarc_policy": "none"}, "dmarc.too_weak"),
    ({"one_click_unsubscribe": False}, "list_unsubscribe.missing"),
])
def test_missing_authentication_zeroes_capacity(kwargs, issue):
    """Not a reputation problem to recover from: it is mail filtered on
    arrival, so the domain must not send at all."""
    domain = Domain(name="get.zolts.dev", mailboxes=[mailbox()], **kwargs)
    assert issue in domain.authentication_issues()
    assert domain.capacity == 0


def test_a_fully_authenticated_domain_sends():
    domain = Domain(name="get.zolts.dev", mailboxes=[mailbox(), mailbox(address="b@x")])
    assert domain.authentication_issues() == []
    assert domain.capacity == 2 * BASE_CAP


# ── fleet and selection ──────────────────────────────────────────────────

def _fleet() -> Fleet:
    return Fleet(domains=[
        Domain(name="get.zolts.dev", mailboxes=[
            mailbox(address="g1@get", provider=Provider.GOOGLE),
            mailbox(address="m1@get", provider=Provider.MICROSOFT),
        ]),
        Domain(name="zolts-hq.com", mailboxes=[
            mailbox(address="g2@hq", provider=Provider.GOOGLE, day=21),
        ]),
    ])


def test_fleet_capacity_is_the_sum_of_its_mailboxes():
    fleet = _fleet()
    assert fleet.capacity == sum(m.capacity for d in fleet.domains for m in d.mailboxes)


def test_selection_segregates_by_recipient_provider():
    """Reputation is scored per provider, so mixing traffic hides a problem at
    one provider until it affects both."""
    fleet = _fleet()
    assert fleet.select(Provider.MICROSOFT).provider is Provider.MICROSOFT
    assert fleet.select(Provider.GOOGLE).provider is Provider.GOOGLE


def test_selection_spreads_load_instead_of_draining_one_mailbox():
    fleet = _fleet()
    first = fleet.select(Provider.GOOGLE)
    first.sent_today = first.capacity      # exhaust it
    second = fleet.select(Provider.GOOGLE)
    assert second is not first


def test_selection_returns_nothing_when_the_fleet_is_exhausted():
    """The caller must treat this as a hard stop. Sending past capacity is how
    a domain is burned."""
    fleet = _fleet()
    for d in fleet.domains:
        for m in d.mailboxes:
            m.sent_today = m.capacity
    assert fleet.select(Provider.GOOGLE) is None
    assert fleet.remaining == 0


def test_a_paused_mailbox_is_skipped_while_its_domain_still_sends():
    """One bad mailbox does not take the domain down, as long as the domain's
    own aggregate stays under the cut-off."""
    fleet = Fleet(domains=[Domain(name="d", mailboxes=[
        mailbox(address="bad@d", sent=1000, complained=5, replied=50),
        mailbox(address="ok@d", sent=1000, complained=0, replied=60),
    ])])
    assert fleet.domains[0].verdict.sending
    assert fleet.select(Provider.GOOGLE).address == "ok@d"


def test_a_domain_over_the_cutoff_pauses_its_healthy_mailboxes_too():
    """Reputation is scored per domain, so a domain past the complaint
    threshold is burned as a whole. A clean mailbox on it is not a way out —
    treating it as one is how the rest of the fleet follows."""
    burned = Domain(name="burned", mailboxes=[
        mailbox(address="bad@burned", sent=1000, complained=5, replied=50),
        mailbox(address="clean@burned", sent=200, complained=0, replied=20),
    ])
    assert not burned.verdict.sending
    assert burned.capacity == 0
    assert Fleet(domains=[burned]).select(Provider.GOOGLE) is None


def test_an_unauthenticated_domain_is_never_selected():
    fleet = Fleet(domains=[Domain(name="d", spf=False, mailboxes=[mailbox()])])
    assert fleet.select(Provider.GOOGLE) is None


# ── ramp ─────────────────────────────────────────────────────────────────

def test_the_ramp_is_staggered_not_a_mass_activation():
    """Activating a fleet at once gives every domain one shared reputation
    history, so a single mistake takes all of them down together."""
    plan = ramp_plan(7)
    assert sum(count for _, count in plan) == 7
    assert max(count for _, count in plan) <= 2
    assert [day for day, _ in plan] == [1, 8, 15, 22]


def test_ramping_nothing_is_an_empty_plan():
    assert ramp_plan(0) == []
