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
from datetime import datetime, timezone
from pathlib import Path

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

AVG_OPPORTUNITY_EUR = 24_000

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
        }

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
                # Pipeline is only reported when the lift clears the MDE. This
                # is the product's central claim, so the fixture enforces it.
                "pipeline": round(delta["absolute"] * n_treat * AVG_OPPORTUNITY_EUR) if significant else None,
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
        "note": "Generated by scripts/build_fixture.py from the repository's own "
                "programs, blueprints and reference core. Observed rates are "
                "illustrative; every derived figure is computed.",
        "programs": programs,
        "blueprints": sorted(catalog.blueprints),
        "plannedPrograms": sorted(catalog.planned_programs),
        "decisions": decisions,
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
