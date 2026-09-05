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
from zolts.experiment import assign, lift, minimum_detectable_effect
from zolts.policy import PACK_V1, ActionContext, Basis, Contact, evaluate

ROOT = Path(__file__).resolve().parent.parent

# Observed outcomes are the one thing the repository cannot derive: no program
# has run. Everything downstream of these — lift, significance, whether a
# figure may be reported at all — is computed, not written.
OBSERVED = {
    "series-a-hiring-surge":       {"enrolled": 1284, "treat": 0.0840, "ctrl": 0.0360, "p95": 41, "spend": 3426.32},
    "workspace-expansion-trigger": {"enrolled": 2140, "treat": 0.1190, "ctrl": 0.0640, "p95": 9,  "spend": 842.16},
    "replenishment-winback":       {"enrolled": 8930, "treat": 0.2140, "ctrl": 0.1880, "p95": 12, "spend": 611.40},
    "new-site-and-reputation":     {"enrolled": 412,  "treat": 0.1460, "ctrl": 0.0510, "p95": 22, "spend": 980.40},
}

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
            mde = minimum_detectable_effect(seen["ctrl"], n_treat, n_control)
            delta = lift(seen["treat"], seen["ctrl"])
            significant = delta["absolute"] > mde

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
                "treat": round(seen["treat"] * 100, 2), "ctrl": round(seen["ctrl"] * 100, 2),
                "absLift": round(delta["absolute"] * 100, 2),
                "relLift": round(seen["treat"] / seen["ctrl"], 2) if seen["ctrl"] else None,
                "mde": round(mde * 100, 2),
                "significant": significant,
                "neededHoldout": needed,
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
                          "significant": False, "neededHoldout": None, "p95": None,
                          "spend": 0.0, "pipeline": None})
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
        "note": "Generated by scripts/build_fixture.py from the repository's own "
                "programs, blueprints and reference core. Observed rates are "
                "illustrative; every derived figure is computed.",
        "programs": programs,
        "blueprints": sorted(catalog.blueprints),
        "plannedPrograms": sorted(catalog.planned_programs),
        "decisions": decisions,
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
                   if p["neededHoldout"] else "no data")
        print(f"{p['key']:<30} {str(p['absLift']) + ' pp':>10}  MDE "
              f"{str(p['mde']) + ' pp':>9}  {verdict}")
