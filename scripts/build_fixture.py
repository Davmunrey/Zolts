#!/usr/bin/env python3
"""Generate the console's data from the repository's own artefacts.

The surface used to carry hand-written programs and a second copy of the
minimum-detectable-effect formula in JavaScript. Two implementations of the
same statistic drift, and the one on screen is the one a customer reads, so the
figures are now derived here — from the real programs, the real blueprints and
the same `zolts.experiment` and `zolts.policy` functions the test suite covers.

The observed rates are the only invented numbers, and they are marked as such:
nothing in this repository has run a program yet.

Usage: PYTHONPATH=. python3 scripts/build_fixture.py
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from zolts import dsl
from zolts.blueprint import load_blueprints
from zolts.catalog import load_catalog
from zolts.experiment import (MIN_CONVERSIONS_PER_ARM, assign, is_resolvable, lift,
                              minimum_detectable_effect)
from zolts.policy import PACK_V1, ActionContext, Basis, Contact, evaluate

ROOT = Path(__file__).resolve().parent.parent

# Observed outcomes are the one thing the repository cannot derive: no program
# has run. Everything downstream of these — lift, significance, whether a
# figure may be reported at all — is computed, not written.
OBSERVED = {
    "series-a-hiring-surge":       {"enrolled": 1284, "treat": 0.0840, "ctrl": 0.0360, "p95": 41, "spend": 3426.32, "unread": 0.31},
    "workspace-expansion-trigger": {"enrolled": 2140, "treat": 0.1190, "ctrl": 0.0640, "p95": 9,  "spend": 842.16, "unread": 0.0},
    "replenishment-winback":       {"enrolled": 8930, "treat": 0.2140, "ctrl": 0.1880, "p95": 12, "spend": 611.40, "unread": 0.62},
    "new-site-and-reputation":     {"enrolled": 412,  "treat": 0.1460, "ctrl": 0.0510, "p95": 22, "spend": 980.40, "unread": 0.08},
}

# `unread` above is the share of a program's conversions that are replies
# nobody read — the provider reported that a human responded and sent no body,
# so an unsubscribe written in prose looks exactly like interest. They still
# count, deliberately; correcting it silently would move every tenant's
# measured lift on a deploy. Decision 16 reports it instead, and the demo shows
# a program where it is 62% next to one where it is zero, because that spread
# is the point.

# What one deal is worth, per archetype. One global average priced an
# ecommerce reorder like an enterprise contract, which is how €24,000 came to
# multiply a replenishment programme (D-49). Chosen, like the rates above; a
# live tenant's console reads the average of their own synced deals and never
# sees these.
DEAL_EUR = {
    "series-a-hiring-surge": 24_000,
    "workspace-expansion-trigger": 9_400,
    "replenishment-winback": 180,
    "new-site-and-reputation": 3_200,
}

# What the last closed period froze for the two programs that have run long
# enough to have one (ADR-043). The arms and the rates come from OBSERVED
# above; the opportunity split and the credit split are observed too, because
# no program has run and neither can be derived from a reply rate. Everything
# the panel shows — the verdict, the lift beside the effect the sample can
# detect, the incremental pipeline or the reason it is withheld — is computed
# by `zolts.report`, the module the runtime freezes with, so the demo cannot
# show a verdict the product would not reach.
# How many enrollments in each arm produced an *opportunity*, not a reply.
# Observed, like the rates above, and separate from them because euros come
# from deals: multiplying a reply-rate lift by a deal size prices a reply as a
# deal, which is what the pipeline figure used to do (D-49).
# Chosen so the demo shows both halves at the level that matters. A programme
# resolves on replies far sooner than on deals, which is the honest shape and
# the reason the pipeline figure is the one that stays empty longest.
OPPORTUNITIES = {
    # A third of the positive replies became a deal, and one control account
    # did: below the five-per-arm floor, so nothing may be declared at all.
    "series-a-hiring-surge":       {"treatment": 34, "control": 1},
    # Expansion inside an existing customer converts about half its replies,
    # and both arms clear the floor. This is the one that resolves.
    "workspace-expansion-trigger": {"treatment": 120, "control": 10},
    # Ecommerce, where an opportunity is a repeat order and most positive
    # replies are one. Eight thousand enrollments and still not significant,
    # because the control arm reorders too.
    "replenishment-winback":       {"treatment": 1180, "control": 121},
    "new-site-and-reputation":     {"treatment": 21, "control": 1},
}

# How many synced deals carry an amount, which is what a live tenant's average
# is taken over. Counted from the split above rather than typed beside it.
DEMO_DEALS_WITH_AMOUNT = sum(sum(o.values()) for o in OPPORTUNITIES.values())

FROZEN = {
    "series-a-hiring-surge": {
        "period": (date(2026, 5, 1), date(2026, 6, 1)),
        "credits": {"email.send": 6120.0, "enrich.email": 3200.0, "agent.generate": 955.0},
        "touches": 6120, "decisions": {"allow": 6120, "deny": 214},
    },
    "workspace-expansion-trigger": {
        "period": (date(2026, 5, 1), date(2026, 6, 1)),
        "credits": {"email.send": 2140.0, "signal.check": 1482.0},
        "touches": 2140, "decisions": {"allow": 2140, "deny": 61},
    },
}

# The tenant's own "before", declared at onboarding (ADR-042). The report
# quotes its digest so the demo shows the pair a partner signs: what the same
# money bought before, and what the holdout says the programs added.
DEMO_BASELINE = {
    "window_start": "2026-01-01", "window_end": "2026-03-31",
    "spend_tools_micros": 1_400_000_000, "spend_data_micros": 900_000_000,
    "spend_sending_micros": 400_000_000, "spend_people_micros": 12_000_000_000,
    "contacted": 4200, "replied": 151, "meetings": 34, "opportunities": 11,
}

# Fixed so the build is reproducible. Quiet-hours logic reads `local_hour`, not
# this, so no decision depends on it.
EVALUATED_AT = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
STATUS = {
    "series-a-hiring-surge": "live", "workspace-expansion-trigger": "live",
    "replenishment-winback": "live", "new-site-and-reputation": "live",
}

# One action per program, evaluated by the real policy engine rather than
# transcribed, so the trace on screen cannot disagree with the runtime.
TRACE = [
    ("Vireo Labs",      "ES", "email",    {"email": Basis.LEGITIMATE_INTEREST}, (), False, 12, 1),
    ("Kestrel Data",    "ES", "email",    {},                                   (), False, 12, 0),
    ("Harbour Systems", "GB", "linkedin", {"linkedin": Basis.LEGITIMATE_INTEREST}, (), False, 12, 2),
    ("Ternary AG",      "DE", "email",    {"email": Basis.CONSENT},             (), True,  12, 0),
    ("Nordwind BV",     "NL", "email",    {"email": Basis.CONSENT},             (), False, 12, 1),
    ("Almacen Sur",     "ES", "voice",    {"voice": Basis.LEGITIMATE_INTEREST}, ("robinson_list_es",), False, 12, 1),
    ("Pallas Retail",   "ES", "whatsapp", {"whatsapp": Basis.LEGITIMATE_INTEREST}, (), False, 12, 1),
    ("Orbit Freight",   "ES", "email",    {"email": Basis.LEGITIMATE_INTEREST}, (), False, 12, 3),
    ("Cobalt Health",   "ES", "whatsapp", {"whatsapp": Basis.CONSENT},          (), False, 22, 0),
]


# Three proposals for the static demo, in the shape the runtime produces.
# The queue is the screen that shows what "agents propose, the runtime
# disposes" actually means, so a demo with an empty one demonstrates the wrong
# half. Illustrative, like the rest of this file, and rendered by exactly the
# code that renders a tenant's real queue.
REVIEW = [
    {
        "id": "prop-demo-1", "agent": "copywriter", "state": "needs_human",
        "channel": "email", "step": "email_1",
        "model": "claude-sonnet-5", "promptVersion": "copywriter-v3",
        "body": "Congratulations on the Series A. Teams that raise at your "
                "stage usually hit the same wall within two quarters: pipeline "
                "coverage stops tracking headcount. Worth twenty minutes?",
        "droppedClaims": ["They are hiring 40 engineers this quarter."],
        "needsHumanReason": "one sentence cited no evidence and was removed",
        "evidence": [
            {"ref": "e1", "source": "filing",
             "text": "Northwind Traders announced a $12m Series A on 14 January."},
            {"ref": "e2", "source": "careers",
             "text": "Northwind Traders lists 9 open engineering roles."},
        ],
        "evalScore": 0.81, "evalFailures": [],
        "gateReason": "eval 0.81 below the 0.85 auto-send threshold",
        "spendVerdict": "yes", "costEur": 0.0104,
    },
    {
        "id": "prop-demo-2", "agent": "copywriter", "state": "needs_human",
        "channel": "email", "step": "email_2",
        "model": "claude-haiku-4-5-20251001", "promptVersion": "copywriter-v3",
        "body": "Following up on the workspace expansion \u2014 happy to share how "
                "three similar teams handled the seat sprawl that follows.",
        "droppedClaims": [],
        "needsHumanReason": None,
        "evidence": [
            {"ref": "e1", "source": "product",
             "text": "Seat count rose from 40 to 96 in 30 days."},
        ],
        "evalScore": 0.88, "evalFailures": ["no_opt_out_line"],
        "gateReason": "eval passed; a required check failed: no_opt_out_line",
        "spendVerdict": "yes", "costEur": 0.0021,
    },
    {
        "id": "prop-demo-3", "agent": "copywriter", "state": "draft",
        "channel": "email", "step": "email_1",
        "model": "claude-haiku-4-5-20251001", "promptVersion": "copywriter-v3",
        "body": "",
        "droppedClaims": [],
        "needsHumanReason": "the spend guard refused and no cheaper model fitted",
        "evidence": [],
        "evalScore": None, "evalFailures": [],
        "gateReason": "spend guard: no (the monthly ceiling is exhausted)",
        "spendVerdict": "no", "costEur": 0.0,
    },
]


def _fleet() -> dict[str, object]:
    """The sending fleet, computed by the code the runtime enforces with.

    Every number here comes out of `zolts.deliverability` — the warm-up curve,
    the reputation factor, the thresholds — rather than being typed. A demo
    that shows a capacity the product would not compute is a demo that lies
    about the one control a buyer's deliverability lead will ask about.

    The mailboxes show the mechanism rather than a happy path: one fully warmed
    and clean, one on day six of warm-up and therefore far below the base cap,
    and one carrying enough complaints to sit in alarm. A panel where
    everything is green demonstrates nothing.
    """
    from zolts.deliverability import Domain, Fleet, Mailbox, Metrics, Provider

    domain = Domain(name="outbound.northbeam.io", mailboxes=[
        Mailbox("ada@outbound.northbeam.io", Provider.GOOGLE, warmup_day=61,
                metrics=Metrics(sent=1180, bounced=9, complained=0, replied=63,
                                unsubscribed=4),
                sent_today=22),
        Mailbox("sam@outbound.northbeam.io", Provider.MICROSOFT, warmup_day=6,
                metrics=Metrics(sent=48, bounced=1, replied=2), sent_today=5),
        # 0.13% complaints: past the 0.1% alarm, short of the 0.3% cut-off.
        Mailbox("lee@outbound.northbeam.io", Provider.OTHER, warmup_day=44,
                metrics=Metrics(sent=760, bounced=12, complained=1, replied=29,
                                unsubscribed=9),
                sent_today=11),
    ])
    fleet = Fleet(domains=[domain])
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
                "address": box.address, "provider": box.provider.value,
                "warmupDay": box.warmup_day, "capacity": box.capacity,
                "remaining": box.remaining, "sentToday": box.sent_today,
                "health": box.verdict.health.value, "rule": box.verdict.rule_key,
            } for box in domain.mailboxes],
        }],
        "pausedDomains": [],
    }


def _prospects() -> dict[str, object]:
    """A worked list, not a clean one.

    Half the contacts are missing something and one is opted out, because a
    prospect list where every row is complete demonstrates nothing about the
    product whose job is completing them.
    """
    people = [
        ("Ada Okonkwo", "ada@northwind.example", "+34600111222", "Northwind Traders", []),
        ("Bram de Vries", None, None, "Northwind Traders", ["email", "phone"]),
        ("Chloé Marchand", "chloe@vireo.example", None, "Vireo Labs", ["phone"]),
        ("Diego Sastre", "diego@kestrel.example", "+34600333444", "Kestrel Data", []),
        ("Eve Lindqvist", None, "+46700555666", "Harbour Systems", ["email"]),
        ("Farid Haddad", "farid@ternary.example", "+4915112233", "Ternary AG", []),
        ("Greta Nowak", "greta@nordwind.example", None, "Nordwind BV", ["phone"]),
        ("Hugo Beltrán", None, None, "Almacen Sur", ["email", "phone"]),
        ("Iris Lambert", "iris@pallas.example", "+34611445566", "Pallas Retail", []),
        ("Jonas Rieger", "jonas@orbit.example", None, "Orbit Freight", ["phone"]),
        ("Kaia Lindholm", None, "+358401234567", "Cobalt Health", ["email"]),
        ("Luca Ferrari", "luca@vireo.example", "+390212345678", "Vireo Labs", []),
        ("Marta Sousa", "marta@harbour.example", None, "Harbour Systems", ["phone"]),
        ("Nils Bergqvist", "nils@ternary.example", "+46812345678", "Ternary AG", []),
    ]
    # The last column is the dossier: one complete, one thin because the
    # verifier struck claims out of it, and two with none. A demo where every
    # account is researched shows nothing about what researching costs.
    accounts = [
        ("Northwind Traders", "northwind.example", "ES", "51-200", "62.01", 2, [], "complete"),
        ("Vireo Labs", "vireo.example", "FR", "11-50", "72.19", 2, [], "thin"),
        ("Kestrel Data", "kestrel.example", "DE", None, None, 1, ["firmographics"], None),
        ("Harbour Systems", "harbour.example", "GB", "201-500", "61.90", 2, [], "complete"),
        ("Ternary AG", "ternary.example", "DE", "501-1000", "62.09", 2, [], None),
        ("Nordwind BV", "nordwind.example", "NL", "51-200", "46.90", 1, [], "complete"),
        ("Almacen Sur", "almacensur.example", "ES", None, None, 1, ["firmographics"], None),
        ("Pallas Retail", "pallas.example", "ES", "201-500", "47.11", 1, [], "thin"),
        ("Orbit Freight", "orbit.example", "ES", "11-50", "52.29", 1, [], None),
        ("Cobalt Health", "cobalt.example", "FI", "1001-5000", "86.10", 1, [], "complete"),
    ]
    return {
        "accounts": [{"id": f"acct-{i}", "name": n, "domain": d, "country": c,
                      "employeeBand": b, "industry": ind, "contacts": k,
                      "missing": m, "dossier": dos}
                     for i, (n, d, c, b, ind, k, m, dos) in enumerate(accounts, 1)],
        "people": [{"id": f"person-{i}", "name": n, "email": e, "phone": ph,
                    "account": a, "country": "ES", "missing": m,
                    "optedOut": n.startswith("Diego")}
                   for i, (n, e, ph, a, m) in enumerate(people, 1)],
        # One of the two researched accounts has had a signal since, so the
        # demo shows the number that matters beside the one that flatters.
        "research": {"accounts": len(accounts),
                     "withDossier": sum(1 for a in accounts if a[7]),
                     "stale": 2,
                     "byState": {"complete": {"count": 4, "stale": 2},
                                 "thin": {"count": 2, "stale": 0}}},
        "missingCounts": {
            "firmographics": sum(1 for a in accounts if a[6]),
            "email": sum(1 for p in people if "email" in p[4]),
            "phone": sum(1 for p in people if "phone" in p[4]),
        },
    }


def _signals_view() -> dict[str, object]:
    """Detections and the latency split, from the real catalogue.

    The split is the demonstration: a buyer's first question about a claimed
    SLA is which half of it you control.
    """
    from zolts.signals import catalogue

    defined = catalogue()
    # Every signal a shipped program triggers on, so the watch list is the
    # order rather than the menu — and the stale column has something in it,
    # because a source that keeps finding things too late is the one to
    # replace and it only shows up if staleness is counted.
    watched = [
        ("funding.round", 412, 3, 0, "2026-09-06"),
        ("hiring.role_opened", 412, 11, 1, "2026-09-06"),
        ("product.limit_hit", 2140, 27, 0, "2026-09-06"),
        ("product.multi_user_signup", 2140, 34, 0, "2026-09-06"),
        ("local.new_location_detected", 168, 6, 2, "2026-09-05"),
        ("local.review_velocity_drop", 168, 4, 0, "2026-09-06"),
        ("local.ads_activity_started", 168, 9, 1, "2026-09-05"),
        ("commerce.cart_abandoned", 8930, 214, 0, "2026-09-06"),
        ("commerce.replenishment_due", 8930, 96, 3, "2026-09-06"),
    ]
    recent = [
        ("funding.round", "press_feed", "Northwind Traders", 0.7, 46, "2026-09-06"),
        ("hiring.role_opened", "jobs_feed", "Vireo Labs", 0.6, 18, "2026-09-06"),
        ("product.limit_hit", "product_events", "Harbour Systems", 0.8, 2, "2026-09-06"),
        ("product.multi_user_signup", "product_events", "Ternary AG", 0.5, 3, "2026-09-06"),
        ("commerce.cart_abandoned", "commerce_events", "Iris Lambert", 0.4, 1, "2026-09-06"),
        ("hiring.role_opened", "jobs_feed", "Cobalt Health", 0.6, 92, "2026-09-06"),
        ("local.ads_activity_started", "ads_intel", "Almacen Sur", 0.5, 141, "2026-09-05"),
        ("commerce.replenishment_due", "commerce_events", "Jonas Rieger", 0.6, 4, "2026-09-05"),
        ("local.new_location_detected", "places_feed", "Pallas Retail", 0.7, 220, "2026-09-05"),
        ("funding.round", "press_feed", "Nordwind BV", 0.9, 31, "2026-09-05"),
        ("product.limit_hit", "product_events", "Orbit Freight", 0.8, 2, "2026-09-04"),
        ("local.review_velocity_drop", "places_feed", "Kestrel Data", 0.5, 74, "2026-09-04"),
    ]
    return {
        "recent": [{"type": t, "source": src, "subject": subj, "strength": st,
                    "observedAt": seen, "detectionMinutes": mins}
                   for t, src, subj, st, mins, seen in recent
                   if t in defined],
        "watched": [{"signal": k, "checks": c, "detected": d, "staleRefused": s,
                     "lastChecked": seen}
                    for k, c, d, s, seen in watched if k in defined],
        # Detection dominates: the sources take longer to notice than the
        # runtime takes to act, which is the honest shape and the one that
        # tells an operator where to spend effort.
        "latency": {"touches": 388, "detectionP95Minutes": 141,
                    "executionP95Minutes": 4, "totalP95Minutes": 145},
    }


def _pipeline(key: str, n_treat: int, n_control: int,
              opps: dict[str, int]) -> dict[str, object]:
    """What the holdout says the deals were worth, or why there is no figure.

    One rule, in `zolts.report`: the increment is on opportunities, and only
    when that comparison clears the effect the sample can detect.
    """
    from zolts.report import Comparison

    comparison = Comparison(treatment_enrolled=n_treat, control_enrolled=n_control,
                            treatment_converted=opps["treatment"],
                            control_converted=opps["control"])
    increment = comparison.incremental_conversions
    deal = DEAL_EUR[key]
    return {
        "pipeline": None if increment is None else increment * deal,
        "pipelineWithheld": (None if increment is not None
                             else f"the opportunity comparison is {comparison.verdict}"),
        "incrementalOpportunities": increment,
        "oppTreat": opps["treatment"], "oppControl": opps["control"],
        "avgOpportunityEur": deal,
        "opportunitiesWithAmount": DEMO_DEALS_WITH_AMOUNT,
    }


def _frozen_reports(program, holdout: float, seen: dict | None) -> list[dict[str, object]]:
    """The frozen reports for one program, as the console's panel reads them.

    Built through `zolts.report`, so the verdict on screen is the verdict the
    runtime would freeze — including the one that resolves nothing.
    """
    from zolts.baseline import from_mapping as baseline_from_mapping
    from zolts.report import BaselineQuote, Comparison, IncrementalityReport

    frozen = FROZEN.get(program.key)
    opps = OPPORTUNITIES.get(program.key, {"treatment": 0, "control": 0})
    if not (frozen and seen):
        return []

    n_control = round(seen["enrolled"] * holdout / 100)
    n_treat = seen["enrolled"] - n_control
    before = baseline_from_mapping(DEMO_BASELINE)
    report = IncrementalityReport(
        program_key=program.key, program_version=program.version,
        spec_hash=program.spec_hash,
        period_start=frozen["period"][0], period_end=frozen["period"][1],
        holdout_pct=holdout,
        primary=Comparison(treatment_enrolled=n_treat, control_enrolled=n_control,
                           treatment_converted=round(seen["treat"] * n_treat),
                           control_converted=round(seen["ctrl"] * n_control)),
        opportunities=Comparison(
            treatment_enrolled=n_treat, control_enrolled=n_control,
            treatment_converted=opps["treatment"],
            control_converted=opps["control"]),
        converted_by_type={"opp_created": opps},
        unread_conversions=round(seen["treat"] * n_treat * seen["unread"]),
        touches_sent=frozen["touches"], decisions=frozen["decisions"],
        credits_by_kind=frozen["credits"],
        average_opportunity_micros=DEAL_EUR[program.key] * 1_000_000,
        opportunities_with_amount=DEMO_DEALS_WITH_AMOUNT,
        baseline=BaselineQuote(
            digest=before.digest(), window_start=before.window_start,
            window_end=before.window_end,
            monthly_spend_micros=before.monthly_spend_micros,
            meetings=before.meetings, opportunities=before.opportunities,
            cost_per_meeting_micros=before.cost_per_meeting_micros,
            cost_per_opportunity_micros=before.cost_per_opportunity_micros))
    body = report.canonical()
    primary = body["primary"]
    pipeline = body["incremental_pipeline_micros"]
    return [{
        "id": report.digest()[:8],
        "periodStart": report.period_start.isoformat(),
        "periodEnd": report.period_end.isoformat(),
        "verdict": report.verdict,
        "digest": report.digest(),
        "liftPp": None if primary["lift"] is None else round(primary["lift"] * 100, 2),
        "mdePp": (None if primary["minimum_detectable_effect"] is None
                  else round(primary["minimum_detectable_effect"] * 100, 2)),
        "nTreat": primary["treatment_enrolled"], "nControl": primary["control_enrolled"],
        "incremental": primary["incremental_conversions"],
        "pipelineEur": None if pipeline is None else round(pipeline / 1_000_000),
        "withheld": body["pipeline_withheld_because"],
        "credits": body["credits_total"],
        "unreadShare": body["unread_share"],
        "baselineDigest": before.digest(),
        "frozenAt": f"{report.period_end.isoformat()}T00:00:00+00:00",
    }]


def _spend_view() -> dict[str, object]:
    """A Growth period part-way through, priced by the real price list."""
    from zolts.billing import PLANS

    plan = PLANS["growth"]
    by_kind = [("email.send", 18400.0, 18400, 16.56),
               ("enrich.email", 9600.0, 1200, 33.60),
               ("agent.generate", 2865.0, 955, 9.55),
               ("signal.check", 1482.0, 2964, 0.0),
               ("enrich.phone", 950.0, 38, 5.32)]
    consumed = sum(k[1] for k in by_kind)
    return {
        "plan": plan.key, "included": float(plan.credits), "consumed": consumed,
        "remaining": float(plan.credits) - consumed,
        "ceiling": float(plan.credits),
        "shareUsed": round(consumed / float(plan.credits), 4),
        "alerting": consumed / float(plan.credits) >= 0.8,
        "periodEnd": "",
        "byKind": [{"kind": k, "credits": c, "events": e, "costEur": eur}
                   for k, c, e, eur in by_kind],
    }


def build() -> dict:
    catalog = load_catalog()
    programs = []

    for program in sorted(catalog.programs, key=lambda p: p.key):
        blueprint = catalog.blueprint_for(program)
        seen = OBSERVED.get(program.key)
        holdout = program.holdout_pct
        entry = {
            "key": program.key,
            "version": program.version,
            "blueprint": blueprint.key,
            "status": STATUS.get(program.key, "draft"),
            "holdout": holdout,
            "metric": program.spec["experiment"]["primary_metric"],
            "signals": [e["signal"] for e in program.spec["trigger"]["events"]],
            "tiers": [t["key"] for t in program.spec["route"]["tiers"]],
            "autoSend": {k: bool(v.get("auto_send")) for k, v in program.spec["plays"].items()},
            "budget": program.spec["budget"]["monthly_credits"],
            "specHash": program.spec_hash,
            "name": program.raw["metadata"]["name"],
            # The document the demo's editor reads and rewrites. A buyer who
            # opens it sees GTM logic as versioned configuration rather than a
            # claim that it is.
            "spec": program.spec,
            "meta": program.raw["metadata"],
            # The signed artefact beside the live figure. Empty for a program
            # no period has closed on, which is every program in week one.
            "reports": _frozen_reports(program, holdout, seen),
        }

        opps = OPPORTUNITIES.get(program.key, {"treatment": 0, "control": 0})
        if seen:
            n_control = round(seen["enrolled"] * holdout / 100)
            n_treat = seen["enrolled"] - n_control
            # Counted from the observed rates rather than written down, like
            # every other derived figure here.
            conversions = (round(n_treat * seen["treat"])
                           + round(n_control * seen["ctrl"]))
            unread = round(conversions * seen["unread"])
            # An arm with too few observed conversions does not establish a
            # baseline, and an MDE computed from one that does not is a number
            # that reads as precise and is not. Same rule as the runtime.
            conv_treat = round(n_treat * seen["treat"])
            conv_control = round(n_control * seen["ctrl"])
            resolvable = is_resolvable(conv_treat, conv_control)
            mde = minimum_detectable_effect(seen["ctrl"], n_treat, n_control)
            delta = lift(seen["treat"], seen["ctrl"])
            significant = resolvable and delta["absolute"] > mde

            # The holdout that would make this lift reportable, found by asking
            # the same function rather than by guessing.
            needed = next(
                (pct for pct in range(int(holdout), 51)
                 if delta["absolute"] > minimum_detectable_effect(
                     seen["ctrl"], seen["enrolled"] - round(seen["enrolled"] * pct / 100),
                     round(seen["enrolled"] * pct / 100))),
                None,
            )

            entry.update({
                "enrolled": seen["enrolled"], "nTreat": n_treat, "nControl": n_control,
                "conversions": conversions,
                "unverifiedConversions": unread,
                "unverifiedShare": (round(unread / conversions, 3) if conversions else None),
                "treat": round(seen["treat"] * 100, 2), "ctrl": round(seen["ctrl"] * 100, 2),
                "absLift": round(delta["absolute"] * 100, 2),
                "relLift": round(seen["treat"] / seen["ctrl"], 2) if seen["ctrl"] else None,
                "mde": round(mde * 100, 2) if resolvable else None,
                "significant": significant,
                "neededHoldout": needed if resolvable else None,
                "unresolvedReason": None if resolvable else (
                    f"fewer than {MIN_CONVERSIONS_PER_ARM} conversions in an arm; "
                    "the baseline is not established"),
                "p95": seen["p95"], "spend": seen["spend"],
                # Pipeline is euros, and euros come from deals. It multiplied
                # the conversion lift — mostly positive replies — by a deal
                # size, pricing a reply as a deal (D-49). It now rests on the
                # opportunity comparison, computed by the same `zolts.report`
                # rules the runtime and the frozen report use, and says why
                # when it rests on nothing.
                **_pipeline(program.key, n_treat, n_control, opps),
                # The whole curve, so the surface can offer a holdout slider
                # without carrying a second copy of the formula in JavaScript.
                "mdeCurve": {
                    str(pct): round(minimum_detectable_effect(
                        seen["ctrl"],
                        seen["enrolled"] - round(seen["enrolled"] * pct / 100),
                        round(seen["enrolled"] * pct / 100)) * 100, 2)
                    for pct in range(5, 26)
                },
            })
        else:
            entry.update({"enrolled": 0, "nTreat": 0, "nControl": 0, "treat": None,
                          "ctrl": None, "absLift": None, "relLift": None, "mde": None,
                          "conversions": 0, "unverifiedConversions": 0,
                          "unverifiedShare": None,
                          "significant": False, "neededHoldout": None, "p95": None,
                          "spend": 0.0, "pipeline": None,
                          "pipelineWithheld": "nobody was enrolled",
                          "incrementalOpportunities": None, "oppTreat": 0, "oppControl": 0,
                          "avgOpportunityEur": DEAL_EUR[program.key],
                          "opportunitiesWithAmount": DEMO_DEALS_WITH_AMOUNT,
                          "unresolvedReason": "no enrollments"})
        programs.append(entry)

    # Evaluated once per jurisdiction so the surface can switch between packs
    # without shipping a second policy engine to the browser.
    decisions = {}
    for jurisdiction in sorted(PACK_V1):
        rows = []
        for name, _home, channel, consent, suppressed, unsub, hour, touches in TRACE:
            contact = Contact(entity_id=name, country=jurisdiction, consent=consent,
                              suppressed_on=frozenset(suppressed),
                              unsubscribed_channels=frozenset({channel}) if unsub else frozenset(),
                              touches_this_week=touches)
            d = evaluate(contact, ActionContext(channel=channel, now=EVALUATED_AT,
                                                local_hour=hour))
            rows.append({"account": name, "channel": channel, "decision": d.decision.value,
                         "rule": d.rule_key, "rationale": d.rationale})
        decisions[jurisdiction] = rows

    # The policy view reads one jurisdiction's evaluation and groups it the way
    # the served console does: by rule, because one contact denied once is a
    # correct denial and one rule denying most of a program is a program to fix.
    policy_rows = decisions["ES"]
    by_rule: dict[str, dict[str, object]] = {}
    for row in policy_rows:
        entry = by_rule.setdefault(row["rule"], {"rule": row["rule"], "allow": 0, "deny": 0})
        entry["allow" if row["decision"] == "allow" else "deny"] += 1
    allowed = sum(int(e["allow"]) for e in by_rule.values())
    denied = sum(int(e["deny"]) for e in by_rule.values())
    policy_view = {
        "recent": [{"decision": r["decision"], "rule": r["rule"], "jurisdiction": "ES",
                    "channel": r["channel"], "subject": r["account"],
                    "rationale": r["rationale"],
                    "decidedAt": EVALUATED_AT.isoformat()} for r in policy_rows],
        "byRule": sorted(by_rule.values(), key=lambda e: -(int(e["deny"]) + int(e["allow"]))),
        "totals": {"allow": allowed, "deny": denied,
                   "denyRate": round(denied / (allowed + denied), 4)
                   if allowed + denied else None},
        "jurisdictions": sorted(PACK_V1),
    }

    # Who authorised what. Every line here is one the runtime actually writes:
    # a key issued, a program published and activated, a draft approved.
    audit_view = {
        "entries": [
            {"actor": a, "action": act, "subject": s, "detail": {}, "at": at}
            for act, a, s, at in [
                ("proposal.approved", "key:3b9fc918", "c41d7e02", "2026-09-06T18:22:00+00:00"),
                ("program.activated", "key:3b9fc918", "series-a", "2026-09-06T18:10:00+00:00"),
                ("program.published", "key:3b9fc918", "series-a", "2026-09-06T18:04:00+00:00"),
                ("sending_domain.verified", "key:0c4471de", "outbound", "2026-09-06T11:47:00+00:00"),
                ("connection.stored", "key:0c4471de", "smartlead", "2026-09-06T11:40:00+00:00"),
                ("proposal.rejected", "key:3b9fc918", "77ba1c39", "2026-09-05T16:31:00+00:00"),
                ("proposal.approved", "key:3b9fc918", "1e9042aa", "2026-09-05T16:28:00+00:00"),
                ("program.paused", "key:3b9fc918", "winback", "2026-09-05T09:15:00+00:00"),
                ("api_key.rotated", "key:0c4471de", "0c4471de", "2026-09-04T11:05:00+00:00"),
                ("program.published", "key:3b9fc918", "winback", "2026-09-03T17:40:00+00:00"),
                ("connection.stored", "key:0c4471de", "hubspot", "2026-09-03T10:02:00+00:00"),
                ("api_key.created", "key:0c4471de", "3b9fc918", "2026-09-01T08:30:00+00:00"),
                ("tenant.created", "key:0c4471de", "northbeam", "2026-09-01T08:12:00+00:00"),
            ]
        ],
        "byAction": [{"action": "proposal.approved", "count": 14},
                     {"action": "program.published", "count": 6},
                     {"action": "connection.stored", "count": 4},
                     {"action": "program.activated", "count": 4},
                     {"action": "proposal.rejected", "count": 3},
                     {"action": "api_key.created", "count": 2},
                     {"action": "program.paused", "count": 2},
                     {"action": "api_key.rotated", "count": 1}],
        "actors": ["key:0c4471de", "key:3b9fc918"],
    }

    # A holdout assignment the reader can check: the same hash the runtime uses.
    sample = assign("acct-demo-1", "series-a-hiring-surge", 10, salt="2026q1")

    # Deliberately no timestamp. This output is committed and CI verifies it
    # matches a fresh build, so a wall-clock field would make every run differ
    # from the last and the staleness guard permanently red. Provenance that
    # actually matters is per-program and deterministic: specHash.
    return {
        # The console reads its chrome from the data. The demo build names
        # itself rather than borrowing a customer's name.
        "tenant": {"name": "Northbeam", "slug": "northbeam", "region": "eu",
                   "blueprint": "b2b-saas-sales-led"},
        "queue": {"pending": 42, "dead": 0, "review": len(REVIEW)},
        "fleet": _fleet(),
        "prospects": _prospects(),
        "signalsView": _signals_view(),
        "spendView": _spend_view(),
        "note": "Generated by scripts/build_fixture.py from the repository's own "
                "programs, blueprints and reference core. Observed rates are "
                "illustrative; every derived figure is computed.",
        "programs": programs,
        "blueprints": sorted(catalog.blueprints),
        "plannedPrograms": sorted(catalog.planned_programs),
        # What the blueprints promise and no file implements yet, with the
        # blueprint that promises each. The served console computes it from
        # the same catalogue, so the demo and a tenant show one section built
        # by one rule rather than two that drift.
        "catalogueGaps": catalog.gaps(),
        # Same derivation the served console uses, so the demo cannot offer a
        # bound the engine would reject either.
        "controls": dsl.controls(),
        "decisions": decisions,
        "policyView": policy_view,
        "auditView": audit_view,
        "review": REVIEW,
        "jurisdictions": sorted(PACK_V1),
        "assignmentSample": {"entity": "acct-demo-1", "bucket": sample.bucket,
                             "variant": sample.variant},
    }


if __name__ == "__main__":
    data = build()
    out = ROOT / "site" / "data" / "console.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    for p in data["programs"]:
        verdict = ("significant" if p["significant"]
                   else f"not significant (needs {p['neededHoldout']}% holdout)"
                   if p["neededHoldout"] else p.get("unresolvedReason") or "no data")
        print(f"{p['key']:<30} {str(p['absLift']) + ' pp':>10}  MDE "
              f"{str(p['mde']) + ' pp':>9}  {verdict}")
