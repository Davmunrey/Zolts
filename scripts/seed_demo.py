"""A demo that survives a buyer's questions.

The smoke run proves the loop is connected with one account. A buyer does not
ask whether the loop is connected; they ask what the number means and how it
was computed, and the answer has to hold when they push on it.

So this seeds a realistic population and then gets out of the way. Enrollment,
holdout assignment, policy, sending, the reply webhook and the measurement are
all the product's own code paths, unchanged. Nothing here writes a lift, a
p-value or a pipeline figure — those come out of the same functions that would
run for a paying customer, and if the sample does not support an effect the
demo says so.

**The population is synthetic and the reply rates are chosen.** That is stated
in the output, on the tenant's own name, and here. What is *not* synthetic is
the measurement: two arms assigned by the real deterministic hash, conversions
recorded through the real webhook path, and a result withheld unless it clears
the effect the sample can actually detect.

    ZOLTS_DATABASE_URL=... ZOLTS_SECRET_KEY=... python3 scripts/seed_demo.py
"""

from __future__ import annotations

import json
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runtime.api import console                                     # noqa: E402
from runtime.config import Settings                                 # noqa: E402
from runtime.connectors import register                             # noqa: E402
from runtime.connectors.fake import FakeConnector                   # noqa: E402
from runtime.db import Database                                     # noqa: E402
from runtime.engine import enroll, inbound                          # noqa: E402
from runtime.engine.worker import Worker                            # noqa: E402
from runtime.provision import (create_tenant, create_webhook_endpoint,  # noqa: E402
                               issue_api_key, store_connection)
from runtime.repo import entities, programs                         # noqa: E402
from zolts import dsl                                               # noqa: E402

PROGRAM = Path(__file__).resolve().parent.parent / "examples/programs/01-b2b-saas-sales-led.yaml"

# Sized so the effect is not merely resolvable but detectable. Two thresholds,
# and the second is the demanding one:
#
#   * Five conversions per arm, below which no effect may be declared at all.
#     A few hundred accounts clears it.
#   * A lift that exceeds the minimum detectable effect at this sample. The
#     seeded effect is 3.5 points; at ~5,000 enrollments the MDE is ~2.8 and
#     the measurement lands just short, reporting a real lift as not
#     significant. That is the product being right, and it is the single most
#     useful thing it does — but a demo that only ever shows "not significant"
#     is a hard sell, so the default is large enough to show both halves.
#
# Roughly 80 seconds at the default.
ACCOUNTS = int(os.environ.get("ZOLTS_DEMO_ACCOUNTS", "12000"))
BATCH = 50
# Chosen, not measured. A buyer who asks "where do these come from" gets this
# sentence rather than a hedge: they are the assumption the demo makes visible,
# and the point of the demo is what the measurement does with them.
CONTROL_REPLY_RATE = 0.04
TREATMENT_REPLY_RATE = 0.075
SEED = 20260906

INDUSTRIES = ("Software", "Fintech", "Logistics", "Healthcare", "Manufacturing")
BANDS = ("11-50", "51-200", "201-500", "501-1000")
# Countries whose email rule the seeded basis actually satisfies. An earlier
# version used NL and IE, which the shipped policy pack does not cover, and DE,
# which requires consent rather than legitimate interest. The gate denied 870
# of 1,484 sends — correctly — and because intent-to-treat counts every
# treatment enrollment whether or not it was contacted, the measured lift went
# negative. That is the product being right and the demo being wrong: a
# measurement of a data gap rather than of the program.
#
# The compliance behaviour is shown separately, from the pack itself, rather
# than by seeding accounts the runtime is obliged to refuse.
COUNTRIES = ("ES", "FR", "GB", "US")
FIRST = ("Dana", "Marc", "Ines", "Tom", "Ana", "Luis", "Eva", "Nils", "Sara", "Iker")
LAST = ("Cruz", "Weber", "Duarte", "Novak", "Rossi", "Bakker", "Moreau", "Lind")


def _sized_for_the_demo(spec: dict) -> dict:
    """Raise the tier capacities to cover the seeded population.

    The example program caps its tiers at 25, 200 and 2,000 contacts a week,
    which is a real sales team's throughput. Enrolling several thousand
    accounts into it and measuring a week later produces a *negative* lift,
    correctly: every treatment enrollment counts whether or not capacity let it
    be contacted, and most were not. That is intent-to-treat, it is the honest
    answer, and it is a genuinely useful thing to show a buyer — but it is a
    measurement of a misconfiguration rather than of the product.

    So the demo sets capacity to what the demo actually sends, which is what a
    customer does with their own team's number. The override is reported in the
    output beside the other assumptions.
    """
    import copy

    sized = copy.deepcopy(spec)
    for tier in sized.get("route", {}).get("tiers", []):
        tier["capacity_per_week"] = ACCOUNTS * 2
    return sized


def _accounts(rng: random.Random) -> list[dict]:
    out = []
    for n in range(ACCOUNTS):
        industry = rng.choice(INDUSTRIES)
        stem = f"{industry.lower()}-{n:03d}"
        out.append({
            "name": f"{industry} Partners {n:03d}",
            "domain": f"{stem}.example",
            "country": rng.choice(COUNTRIES),
            "employee_band": rng.choice(BANDS),
            "industry": industry,
            "email": f"{rng.choice(FIRST).lower()}.{rng.choice(LAST).lower()}@{stem}.example",
            "full_name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
            # Enough to trigger the program's `payload.amount_usd >= 5000000`
            # for most, and below it for the rest, so enrollment is a real
            # filter rather than a formality.
            "amount_usd": rng.choice([2_000_000, 6_000_000, 9_000_000, 14_000_000,
                                      22_000_000, 40_000_000]),
        })
    return out


def main() -> int:
    rng = random.Random(SEED)
    settings = Settings.from_env()
    db = Database(settings.database_url, settings.app_database_url)
    db.migrate()
    db.grant_app_role()
    register(FakeConnector())

    program = dsl.load(PROGRAM)
    spec = _sized_for_the_demo(program.spec)
    tenant = create_tenant(db, slug=f"demo-{uuid.uuid4().hex[:8]}",
                           name="Northwind Traders (demo data)", region="eu",
                           blueprint_id=program.raw["metadata"]["blueprint"])
    tid = str(tenant["id"])
    store_connection(db, tid, provider="fake", secret="none",
                     secret_key=settings.secret_key)
    create_webhook_endpoint(db, tid, provider="smartlead", secret_key=settings.secret_key)

    with db.tenant_tx(tid) as cur:
        row = programs.publish(cur, tid, key=program.key, version=program.version,
                               spec=spec, spec_hash=program.spec_hash, status="draft")
        programs.activate(cur, str(row["id"]))

    print(f"seeding {ACCOUNTS} accounts…", file=sys.stderr)
    enrolled = 0
    observed = datetime.now(timezone.utc) - timedelta(days=21)
    records = _accounts(rng)
    # Batched, because a transaction per account spends the whole run on round
    # trips. Small enough that a failure loses one batch rather than the run.
    for start in range(0, len(records), BATCH):
        with db.tenant_tx(tid) as cur:
            for record in records[start:start + BATCH]:
                account = entities.upsert_account(
                    cur, tid, name=record["name"], domain=record["domain"],
                    country=record["country"], employee_band=record["employee_band"],
                    industry=record["industry"])
                person = entities.upsert_person(
                    cur, tid, email=record["email"], full_name=record["full_name"],
                    country=record["country"],
                    consent_state={"email": {"basis": "legitimate_interest",
                                             "source": "demo"}})
                entities.link(cur, tid, str(person["id"]), str(account["id"]),
                              buying_role="economic")
                result = enroll.ingest(
                    cur, tid, entity_type="account", entity_id=str(account["id"]),
                    type="funding.round", strength=0.72, half_life_h=720, source="demo",
                    legal_basis="legitimate_interest",
                    payload={"stage": "series_a", "amount_usd": record["amount_usd"]},
                    observed_at=observed, dedupe_key=f"demo-{uuid.uuid4().hex}")
                enrolled += len(result.enrollments)
        print(f"  {min(start + BATCH, len(records))}/{len(records)}", file=sys.stderr)

    print(f"draining the outbox ({enrolled} enrollments)…", file=sys.stderr)
    worker = Worker(db, secret_key=settings.secret_key)
    # The ceiling is a guard against a loop that never terminates, not a budget.
    # Written as a flat 200 first, which at a batch of 25 stopped after exactly
    # 5,000 actions and left a third of the treatment arm uncontacted — the
    # measured lift then reported the dilution rather than the program.
    max_ticks = ACCOUNTS + 200
    ticks, sent = 0, 0
    while ticks < max_ticks:
        tick = worker.tick([tid])
        ticks += 1
        if not tick.claimed:
            break
        sent += tick.succeeded
    else:
        print(f"warning: stopped after {ticks} ticks with work still queued",
              file=sys.stderr)

    replies = _deliver_replies(db, tid, rng)

    with db.tenant_tx(tid) as cur:
        view = console.build(cur, {"name": tenant["name"], "slug": tenant["slug"],
                                   "region": "eu",
                                   "blueprint_id": program.raw["metadata"]["blueprint"]})
    key = issue_api_key(db, tid, "demo", [])

    print(json.dumps({
        "tenant": {"id": tid, "slug": tenant["slug"], "name": tenant["name"]},
        "api_key": key.token,
        "console": "GET /console with that key",
        "seeded": {"accounts": ACCOUNTS, "enrollments": enrolled,
                   "touches_sent": sent, "replies_delivered": replies},
        "assumptions": {
            "control_reply_rate": CONTROL_REPLY_RATE,
            "treatment_reply_rate": TREATMENT_REPLY_RATE,
            "tier_capacity_per_week": ACCOUNTS * 2,
            "avg_opportunity_eur": console.AVG_OPPORTUNITY_EUR,
            "note": "the population, these two rates and the tier capacity are "
                    "synthetic and chosen. Capacity is raised from the example "
                    "program's 25/200/2000 so every treatment enrollment is actually "
                    "contacted; left as shipped, the measured lift goes negative "
                    "because intent-to-treat counts enrollments capacity never "
                    "reached. Everything downstream — arm assignment, the conversion "
                    "path, and the measurement below — is the product's own code. The "
                    "pipeline figure multiplies the measured lift by "
                    f"{console.AVG_OPPORTUNITY_EUR} EUR per opportunity, which is a "
                    "constant in the console and the first number a buyer should "
                    "replace with their own",
        },
        # From the shipped pack, not from seeded data. The demo does not need to
        # manufacture a denial to show that jurisdiction decides: it can read
        # out what the pack requires and let a buyer check it against their own
        # counsel's answer.
        "policy_pack": _pack_summary(),
        "measurement": [
            {k: p.get(k) for k in ("key", "enrolled", "holdout", "metric",
                                   "absLift", "mde", "significant", "pipeline",
                                   "unresolvedReason")}
            for p in view["programs"]],
    }, indent=2, default=str))
    db.close()
    return 0


def _pack_summary() -> dict[str, str]:
    """What each jurisdiction requires for email, straight out of the pack."""
    from zolts.policy import PACK_V1, UNKNOWN_JURISDICTION

    summary = {c: (r.required_basis.get("email").value
                   if r.required_basis.get("email") else "none")
               for c, r in sorted(PACK_V1.items())}
    summary["__unknown__"] = (
        "consent — an unknown jurisdiction is not an implicit allow, so an "
        "account the pack does not cover is refused rather than sent to")
    return summary


def _deliver_replies(db: Database, tid: str, rng: random.Random) -> int:
    """Replies through the real webhook path, at a rate that differs by arm.

    Only the treatment arm has touches at all — the control arm is never
    contacted, which is the point of a holdout — so a control conversion is
    recorded directly against its enrollment, exactly as a CRM-sourced outcome
    would be for a customer.
    """
    delivered = 0
    with db.tenant_tx(tid) as cur:
        cur.execute(
            "select t.idempotency_key from touch t"
            " join enrollment e on e.id = t.enrollment_id"
            " where t.status = 'sent' and e.variant = 'treatment'")
        keys = [r["idempotency_key"] for r in cur.fetchall()]

    for idempotency_key in keys:
        if rng.random() >= TREATMENT_REPLY_RATE:
            continue
        with db.tenant_tx(tid) as cur:
            event = inbound.store(cur, tid, provider="smartlead", signature_ok=True,
                                  payload={"event_type": "reply",
                                           "id": f"demo-{uuid.uuid4().hex}",
                                           "lead": {"custom_fields": {
                                               "zolts_idempotency_key": idempotency_key}}})
            inbound.apply(cur, tid, event)
        delivered += 1

    control_rng = random.Random(SEED + 1)
    with db.tenant_tx(tid) as cur:
        cur.execute("select id, entity_id from enrollment"
                    " where variant = 'control' and entity_type = 'account'")
        controls = cur.fetchall()
        for row in controls:
            if control_rng.random() >= CONTROL_REPLY_RATE:
                continue
            cur.execute(
                "insert into outcome (tenant_id, enrollment_id, account_id, type,"
                " occurred_at, source, dedupe_key)"
                " values (%s,%s,%s,'reply_positive',now(),'demo',%s)",
                (tid, row["id"], row["entity_id"], f"demo-control-{row['id']}"))
            delivered += 1
    return delivered


if __name__ == "__main__":
    raise SystemExit(main())
