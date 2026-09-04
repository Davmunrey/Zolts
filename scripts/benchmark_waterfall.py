#!/usr/bin/env python3
"""Measure what the waterfall optimiser actually saves against a static order.

docs/07 claims 30-50% and docs/13 sets ">=30% reduction in cost per verified
contact" as a phase 2 exit criterion. This script measures it rather than
asserting it, so the claim in the plan can be corrected if the model does not
support it.

Baseline: the hand-configured static waterfall teams actually run — one global
premium-first order, never revisited per cohort.
"""

from __future__ import annotations

import statistics

from zolts.waterfall import Provider, optimise, static_waterfall

# Cohorts reflect the real driver of hit-rate variance: geography, company size
# and how obscure the contact's role is. Numbers are plausible ranges from
# public provider coverage claims, deliberately conservative.
COHORTS = [
    "es_smb", "es_mid", "de_mid", "de_ent", "fr_mid",
    "gb_mid", "gb_ent", "us_smb", "us_mid", "us_ent",
    "nl_smb", "it_mid", "latam_smb", "nordics_mid", "pl_smb",
]

PROVIDERS = [
    Provider(
        key="broad_cheap", unit_cost=0.008, accuracy=0.91,
        hit_rates={
            "es_smb": 0.72, "es_mid": 0.64, "de_mid": 0.41, "de_ent": 0.38,
            "fr_mid": 0.55, "gb_mid": 0.61, "gb_ent": 0.52, "us_smb": 0.68,
            "us_mid": 0.60, "us_ent": 0.55, "nl_smb": 0.58, "it_mid": 0.62,
            "latam_smb": 0.74, "nordics_mid": 0.44, "pl_smb": 0.66,
        },
    ),
    Provider(
        key="premium", unit_cost=0.045, accuracy=0.97,
        hit_rates={
            "es_smb": 0.61, "es_mid": 0.78, "de_mid": 0.83, "de_ent": 0.91,
            "fr_mid": 0.80, "gb_mid": 0.86, "gb_ent": 0.93, "us_smb": 0.72,
            "us_mid": 0.85, "us_ent": 0.88, "nl_smb": 0.69, "it_mid": 0.71,
            "latam_smb": 0.44, "nordics_mid": 0.76, "pl_smb": 0.58,
        },
    ),
    Provider(
        key="regional", unit_cost=0.021, accuracy=0.94,
        hit_rates={
            "es_smb": 0.68, "es_mid": 0.59, "de_mid": 0.35, "de_ent": 0.29,
            "fr_mid": 0.47, "gb_mid": 0.33, "gb_ent": 0.24, "us_smb": 0.31,
            "us_mid": 0.26, "us_ent": 0.22, "nl_smb": 0.51, "it_mid": 0.64,
            "latam_smb": 0.71, "nordics_mid": 0.48, "pl_smb": 0.63,
        },
    ),
    Provider(
        key="verifier", unit_cost=0.003, accuracy=0.99,
        hit_rates={c: 0.18 for c in COHORTS},  # narrow but nearly free
    ),
]


def main() -> None:
    rows, savings = [], []
    for cohort in COHORTS:
        opt = optimise(PROVIDERS, cohort)
        static = static_waterfall(PROVIDERS, cohort)
        saving = 1 - opt.cost_per_verified_contact / static.cost_per_verified_contact
        savings.append(saving)
        rows.append((cohort, static.cost_per_verified_contact,
                     opt.cost_per_verified_contact, saving, opt.order[0]))

    width = max(len(c) for c in COHORTS)
    print(f"{'cohort':<{width}}  {'static':>8}  {'optimised':>9}  {'saving':>7}  first call")
    print("-" * (width + 42))
    for cohort, static_cost, opt_cost, saving, first in rows:
        print(f"{cohort:<{width}}  {static_cost:>8.4f}  {opt_cost:>9.4f}  "
              f"{saving:>6.1%}  {first}")

    print("-" * (width + 42))
    print(f"{'mean':<{width}}  {'':>8}  {'':>9}  {statistics.mean(savings):>6.1%}")
    print(f"{'median':<{width}}  {'':>8}  {'':>9}  {statistics.median(savings):>6.1%}")
    print(f"{'min':<{width}}  {'':>8}  {'':>9}  {min(savings):>6.1%}")
    print(f"{'max':<{width}}  {'':>8}  {'':>9}  {max(savings):>6.1%}")
    below_30 = [c for c, s in zip(COHORTS, savings) if s < 0.30]
    print(f"\ncohorts below the 30% phase-2 exit criterion: "
          f"{len(below_30)}/{len(COHORTS)} {below_30}")


if __name__ == "__main__":
    main()
