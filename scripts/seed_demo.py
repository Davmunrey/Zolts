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
from runtime.connectors import sync                                # noqa: E402
from runtime.connectors.crm import (Capabilities, CrmAccount, CrmContact,  # noqa: E402
                                    CrmOpportunity, DealStatus)
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
# Raised from twelve thousand when the audience began to filter for real: the
# programme is for software companies of 51-500 people in six countries, and a
# universe of twelve thousand leaves four thousand enrolled — an arm too small
# to resolve a 3.5 point effect, so the demo showed only the half that says
# "not yet". The rates were not touched to fix that; the population was.
#
# Roughly four minutes at the default.
ACCOUNTS = int(os.environ.get("ZOLTS_DEMO_ACCOUNTS", "36000"))
BATCH = 50
# Chosen, not measured. A buyer who asks "where do these come from" gets this
# sentence rather than a hedge: they are the assumption the demo makes visible,
# and the point of the demo is what the measurement does with them.
CONTROL_REPLY_RATE = 0.04
TREATMENT_REPLY_RATE = 0.075
# How often a positive reply becomes an opportunity, and what one is worth.
# Chosen, like the rates above, and declared in the seeder's own output: euros
# come from deals, so the pipeline figure rests on these rather than on the
# reply rate multiplied by a constant that used to live in the runtime (D-49).
# The same rate in both arms: the programme's claim is that it produces more
# replies, not that it makes a reply worth more.
REPLY_TO_OPPORTUNITY = 0.34
# How many accounts arrive from the CRM already in a live deal. They match the
# programme's audience in every other way, so what leaves them alone is the
# exclusion rather than the filter.
IN_A_DEAL = 40
AVG_OPPORTUNITY_EUR = 24_000
SEED = 20260906

# The demo tenant's account universe, and it is not uniform: a GTM team that
# sells to software companies of 51-500 people does not hold twelve thousand
# random companies. Weighted towards the audience the flagship programme
# declares, which is what makes the audience a filter rather than a wall — and
# declared in the seeder's output like every other assumption.
#
# It used to be uniform *and* the industry never reached the row: the seeder
# passed `industry=` and `upsert_account` reads `industry_code`, so every
# account had none and the audience — `industry_code = 'software'` — matched
# nobody. From the day the audience block began to execute, this script seeded
# a tenant that enrolled zero accounts (D-50).
INDUSTRIES = (("Software", 62), ("Fintech", 14), ("Logistics", 10),
              ("Healthcare", 8), ("Manufacturing", 6))
BANDS = (("11-50", 22), ("51-200", 41), ("201-500", 27), ("501-1000", 10))
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


def _weighted(rng: random.Random, choices: tuple) -> str:
    """One value, at the declared share. Seeded, so the population is the same
    on every run and the numbers a buyer was shown can be reproduced."""
    total = sum(weight for _, weight in choices)
    cut = rng.uniform(0, total)
    running = 0.0
    for value, weight in choices:
        running += weight
        if cut <= running:
            return value
    return choices[-1][0]


def _accounts(rng: random.Random) -> list[dict]:
    out = []
    for n in range(ACCOUNTS):
        industry = _weighted(rng, INDUSTRIES)
        stem = f"{industry.lower()}-{n:03d}"
        out.append({
            "name": f"{industry} Partners {n:03d}",
            "domain": f"{stem}.example",
            "country": rng.choice(COUNTRIES),
            "employee_band": _weighted(rng, BANDS),
            # `industry_code`, because that is the column the audience reads.
            # `industry` was accepted by nothing and silently dropped (D-50).
            "industry_code": industry.lower(),
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

    # Before any signal, because that is the order a customer's system works
    # in: the programme's audience decides by the `opportunity` table and
    # refuses to enrol anybody until a source that reads deals has run
    # (ADR-038). Without it this script enrolled nobody at all (D-50).
    synced = sync.pull(db, tid, credential="none", provider="demo-crm", source=_DemoCrm())

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
                    industry_code=record["industry_code"])
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

    replies, won = _deliver_replies(db, tid, rng)

    # The second pass. A deal is worth something and the runtime carries no
    # average deal size of its own, so the amounts come from the CRM exactly
    # as a customer's would (D-49).
    with db.tenant_tx(tid) as cur:
        cur.execute(
            "select a.domain from touch t join enrollment e on e.id = t.enrollment_id"
            " join account a on a.id = e.entity_id where t.idempotency_key = any(%s)",
            (won,))
        domains = [r["domain"] for r in cur.fetchall() if r["domain"]]
    deals = [(domain, int(AVG_OPPORTUNITY_EUR * rng.uniform(0.55, 1.6)) * 1_000_000)
             for domain in domains]
    if deals:
        synced = sync.pull(db, tid, credential="none", provider="demo-crm",
                           source=_DemoCrm(won=deals))

    with db.tenant_tx(tid) as cur:
        # The tenant's own row. A dictionary of labels raises here — the spend
        # view reads a billing period keyed by id — and this script is the sales
        # demo, so it crashed at its last step and nothing in CI ran it (D-50).
        cur.execute("select * from tenant where id = %s", (tid,))
        view = console.build(cur, dict(cur.fetchone()))
    key = issue_api_key(db, tid, "demo", [])

    print(json.dumps({
        "tenant": {"id": tid, "slug": tenant["slug"], "name": tenant["name"]},
        "api_key": key.token,
        "console": "GET /console with that key",
        "seeded": {"accounts": ACCOUNTS, "enrollments": enrolled,
                   "touches_sent": sent, "replies_delivered": replies,
                   # What the CRM delivered, and what the audience did with it.
                   "crm_accounts": synced.accounts, "open_deals": synced.open_deals,
                   "left_alone_because_in_a_deal": IN_A_DEAL},
        "assumptions": {
            "control_reply_rate": CONTROL_REPLY_RATE,
            "treatment_reply_rate": TREATMENT_REPLY_RATE,
            "tier_capacity_per_week": ACCOUNTS * 2,
            "population": {"industries": dict(INDUSTRIES), "bands": dict(BANDS),
                           "note": "shares, not counts: the demo tenant's universe is "
                                   "weighted towards the audience its programme declares, "
                                   "the way a real account list is"},
            "reply_to_opportunity_rate": REPLY_TO_OPPORTUNITY,
            "avg_opportunity_eur": AVG_OPPORTUNITY_EUR,
            "note": "the population, these two rates and the tier capacity are "
                    "synthetic and chosen. Capacity is raised from the example "
                    "program's 25/200/2000 so every treatment enrollment is actually "
                    "contacted; left as shipped, the measured lift goes negative "
                    "because intent-to-treat counts enrollments capacity never "
                    "reached. Everything downstream — arm assignment, the conversion "
                    "path, and the measurement below — is the product's own code. The "
                    "pipeline figure rests on the opportunity arms and on the average "
                    "of the deals seeded above, which is what a live tenant's own CRM "
                    "supplies; the runtime carries no deal size of its own",
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


class _DemoCrm:
    """What the tenant's CRM holds, in two passes.

    First the accounts already in a live deal, before anything is enrolled.
    Then, once the programme has produced replies, the deals those replies
    became — because that is the order a customer's system works in, and
    because a deal is worth something and the runtime carries no average deal
    size of its own (D-49).

    The flagship programme's audience excludes them, and that exclusion is
    refused outright until a source that reads deals has actually run
    (ADR-038). This script never ran one, so from the day that guard shipped
    the demo enrolled nobody and printed a tenant with no measurement at all
    (D-50) — and nothing in CI seeded a demo, so nothing said so.

    Deliberately a handful of accounts rather than the whole population: they
    are the ones a buyer should ask about, and the answer is that the product
    left them alone.
    """

    capabilities = Capabilities(provider="demo-crm", reads_opt_out=False,
                                reads_opportunities=True)

    def __init__(self, won: list[tuple[str, int]] | None = None) -> None:
        # (domain, amount in micros) for the deals the programme produced.
        # Empty on the first pass, which runs before anything is enrolled.
        self.won = won or []

    def accounts(self, credential: str):
        for i in range(IN_A_DEAL):
            yield CrmAccount(external_id=f"deal-{i}", name=f"Account in a deal {i}",
                             domain=f"in-a-deal-{i}.example", country="ES",
                             employee_band="51-200", industry="software")
        # Keyed by the domain the account was seeded with, so the sync attaches
        # a CRM id to the row that already exists rather than creating a second
        # company with the same name.
        for domain, _ in self.won:
            yield CrmAccount(external_id=f"won-{domain}", name=domain, domain=domain,
                             country="ES", employee_band="51-200", industry="software")

    def contacts(self, credential: str):
        for i in range(IN_A_DEAL):
            yield CrmContact(external_id=f"deal-c{i}", email=f"buyer{i}@in-a-deal-{i}.example",
                             full_name=f"Buyer {i}", country="ES",
                             account_external_id=f"deal-{i}")

    def opportunities(self, credential: str):
        for i in range(IN_A_DEAL):
            yield CrmOpportunity(external_id=f"deal-o{i}", account_external_id=f"deal-{i}",
                                 name=f"Renewal {i}", stage="Negotiation",
                                 status=DealStatus.OPEN)
        for domain, amount in self.won:
            yield CrmOpportunity(external_id=f"won-o-{domain}",
                                 account_external_id=f"won-{domain}",
                                 name=f"New business · {domain}", stage="Discovery",
                                 status=DealStatus.OPEN, amount_micros=amount,
                                 currency="EUR")


def _deliver_replies(db: Database, tid: str, rng: random.Random) -> int:
    """Replies through the real webhook path, at a rate that differs by arm.

    Only the treatment arm has touches at all — the control arm is never
    contacted, which is the point of a holdout — so a control conversion is
    recorded directly against its enrollment, exactly as a CRM-sourced outcome
    would be for a customer.
    """
    delivered = 0
    won: list[str] = []
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
            if rng.random() < REPLY_TO_OPPORTUNITY:
                # Through the same signed path the reply came in on. A deal
                # event is a conversion the runtime records; nothing here
                # writes an outcome for an arm that has a touch to hang one on.
                deal = inbound.store(cur, tid, provider="smartlead", signature_ok=True,
                                     payload={"event_type": "deal.creation",
                                              "id": f"demo-deal-{uuid.uuid4().hex}",
                                              "lead": {"custom_fields": {
                                                  "zolts_idempotency_key": idempotency_key}}})
                inbound.apply(cur, tid, deal)
                won.append(idempotency_key)
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
            if control_rng.random() < REPLY_TO_OPPORTUNITY:
                # The same exception, for the same reason: a control account is
                # never contacted, so there is no touch for a webhook to name.
                cur.execute(
                    "insert into outcome (tenant_id, enrollment_id, account_id, type,"
                    " occurred_at, source, dedupe_key)"
                    " values (%s,%s,%s,'opp_created',now(),'demo',%s)",
                    (tid, row["id"], row["entity_id"], f"demo-control-opp-{row['id']}"))
            delivered += 1
    return delivered, won


if __name__ == "__main__":
    raise SystemExit(main())
