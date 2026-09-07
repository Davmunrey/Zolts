"""Operator commands.

Deliberately small. Anything that changes tenant data belongs behind the API,
where it is authenticated and audited; these are the operations that exist
before a tenant does, plus the two long-running processes.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from runtime.config import Settings
from runtime.connectors import install_default_connectors
from runtime.connectors.dataprovider import iter_fields as _iter_fields
from runtime.connectors.dataprovider import providers as providers_registered
from runtime.crypto import Keyring
from runtime.db import Database, one
from runtime.provision import (create_tenant, create_webhook_endpoint, issue_api_key,
                               store_connection)
from runtime.repo import ledger, programs


def _db(settings: Settings) -> Database:
    return Database(settings.database_url, settings.app_database_url)


def quickstart(db: Database, *, secret_key: "str | Keyring", slug: str, name: str, region: str,
               blueprint: str, base_url: str) -> dict[str, Any]:
    """Everything a first run needs, in one command.

    Provisions a tenant, publishes and activates the repository's example
    programs, opens a webhook endpoint and issues a key. It deliberately does
    not create a connector: a tenant with no connection fails loudly on
    dispatch, and that is the correct first experience — the operator should
    have to say which provider is theirs rather than discover later that
    nothing was ever sent.
    """
    from zolts import dsl

    tenant = create_tenant(db, slug=slug, name=name, region=region, blueprint_id=blueprint)
    tenant_id = str(tenant["id"])
    program_dir = Path(__file__).resolve().parent.parent / "examples" / "programs"
    if not program_dir.is_dir():
        raise FileNotFoundError(
            f"{program_dir} does not exist, so this first run would create a tenant "
            "with no programs and report success; the image was built without the "
            "example programs")
    published = []
    for path in sorted(program_dir.glob("*.yaml")):
        program = dsl.load(path)
        findings = dsl.lint(program)
        with db.tenant_tx(tenant_id) as cur:
            row = programs.publish(cur, tenant_id, key=program.key, version=program.version,
                                   spec=program.spec, spec_hash=program.spec_hash,
                                   status="draft", created_by="quickstart",
                                   metadata=program.raw["metadata"])
            programs.activate(cur, str(row["id"]))
        published.append({"key": program.key, "version": program.version, "lint": findings})

    key = issue_api_key(db, tenant_id, "quickstart", [])
    hook = create_webhook_endpoint(db, tenant_id, provider="smartlead", secret_key=secret_key)
    return {
        "tenant": {"id": tenant_id, "slug": slug, "name": name},
        "programs": published,
        "api_key": key.token,
        "console": f"{base_url}/console",
        "webhook": {"url": f"{base_url}/v1{hook['path']}", "secret": hook["secret"]},
        "next": [
            f"open the console:  {base_url}/console  (sign in with the key above)",
            "connect a provider: printf %s \"$TOKEN\" | python3 -m runtime.cli connect"
            f" --tenant {tenant_id} --provider smartlead --config '{{\"campaign_id\":\"...\"}}'",
            "run the worker:     python3 -m runtime.cli worker",
        ],
        "note": "the API key and the webhook secret are shown once and are not recoverable",
    }


def dataprovider_fields() -> tuple[str, ...]:
    return tuple(_iter_fields())


def _render_rotation(state, done) -> str:
    """What an operator needs: whether it is finished, and what is left."""
    lines = []
    if done is not None:
        lines.append(f"re-sealed {done.rows} credential(s) across {done.tenants} tenant(s)")
        if done.failed:
            lines.append(f"FAILED   {done.failed} credential(s) no configured key opens: "
                         + ", ".join(done.failures[:5]))
    lines.append(f"total {state.total}  on the current key {state.on_primary}"
                 f"  on a previous key {state.on_previous}"
                 f"  unknown {state.unknown}  unopenable {state.unopenable}")
    if not state.total:
        lines.append("nothing is sealed yet; there is nothing to rotate")
    elif state.unopenable and state.unopenable + state.on_primary == state.total:
        lines.append(f"{state.unopenable} credential(s) cannot be opened by any key this "
                     "deployment holds. Re-running will not help: either configure the key "
                     "that sealed them in ZOLTS_PREVIOUS_SECRET_KEYS, or re-enter those "
                     "credentials — nothing in this database can recover them")
    elif state.complete:
        lines.append("rotation complete — remove the old key from "
                     "ZOLTS_PREVIOUS_SECRET_KEYS, which is what actually retires it")
    else:
        lines.append("rotation incomplete — re-run; the old key must stay configured")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zolts", description="Zolts runtime operations")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="apply pending migrations")

    tenant = sub.add_parser("create-tenant", help="provision a tenant")
    tenant.add_argument("--slug", required=True)
    tenant.add_argument("--name", required=True)
    tenant.add_argument("--region", required=True, choices=["eu", "us", "apac"])
    tenant.add_argument("--blueprint", required=True)
    tenant.add_argument("--compliance-tier", default="standard")

    key = sub.add_parser("issue-key", help="issue an API key; shown once")
    key.add_argument("--tenant", required=True)
    key.add_argument("--name", default="default")
    key.add_argument("--scope", action="append", default=[])

    conn = sub.add_parser("connect", help="store a provider credential, sealed")
    conn.add_argument("--tenant", required=True)
    conn.add_argument("--provider", required=True)
    conn.add_argument("--display-name", default="default")
    conn.add_argument("--config", default="{}")
    # The secret is read from stdin so it never reaches a shell history file or
    # a process listing.
    conn.add_argument("--secret-stdin", action="store_true", default=True)

    worker = sub.add_parser("worker", help="run the execution loop")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--interval", type=float, default=2.0)

    quick = sub.add_parser("quickstart",
                           help="provision a tenant, publish the example programs, print a key")
    quick.add_argument("--slug", default="demo")
    quick.add_argument("--name", default="Demo Company")
    quick.add_argument("--region", default="eu", choices=["eu", "us", "apac"])
    quick.add_argument("--blueprint", default="b2b-saas-sales-led")
    quick.add_argument("--base-url", default="http://localhost:8000")

    sync = sub.add_parser("sync", help="pull a CRM into the canonical entities")
    sync.add_argument("--tenant", required=True)
    sync.add_argument("--provider", default="hubspot")
    sync.add_argument("--limit", type=int, default=100)

    mapping = sub.add_parser("crm-mapping", help="publish a CRM mapping from a file")
    mapping.add_argument("--tenant", required=True)
    mapping.add_argument("--file", required=True)

    sub.add_parser("liveness", help="is this deployment draining its outbox?")

    close = sub.add_parser("close-period", help="close a tenant's billing period")
    close.add_argument("--tenant", required=True)

    domain = sub.add_parser(
        "sending-domain",
        help="register a sending domain, or confirm its authentication")
    domain.add_argument("--tenant", required=True)
    domain.add_argument("--name", required=True)
    domain.add_argument("--spf", action="store_true", help="SPF is published")
    domain.add_argument("--dkim", action="store_true", help="DKIM is published")
    domain.add_argument("--dmarc", default="none",
                        choices=["none", "quarantine", "reject"])
    domain.add_argument("--one-click-unsubscribe", action="store_true")
    domain.add_argument("--resume", action="store_true",
                        help="lift a pause a cut-off applied")

    box = sub.add_parser("mailbox", help="register a sending mailbox")
    box.add_argument("--tenant", required=True)
    box.add_argument("--address", required=True)
    box.add_argument("--provider", default="other",
                     choices=["google", "microsoft", "other"],
                     help="the recipients this mailbox sends to, not its host")
    box.add_argument("--warmup-started",
                     help="ISO date warm-up began; defaults to today, which is "
                          "the cautious reading and sends less")
    box.add_argument("--pause", action="store_true")
    box.add_argument("--resume", action="store_true")

    dp = sub.add_parser("data-provider", help="register a data provider to buy fields from")
    dp.add_argument("--tenant", required=True)
    dp.add_argument("--key", required=True, help="must match a registered connector")
    dp.add_argument("--fields", required=True,
                    help="comma-separated: email, phone, firmographics")
    dp.add_argument("--cost-micros", type=int, required=True,
                    help="what one call costs us, in micros of EUR. The waterfall "
                         "optimises against this, so a wrong number buys the wrong order")
    dp.add_argument("--accuracy", type=float, default=0.90)
    dp.add_argument("--hit-rate", type=float, default=0.30,
                    help="conservative starting estimate, used only until this "
                         "provider has made enough calls to be measured")
    dp.add_argument("--billed-on-miss", action="store_true")
    dp.add_argument("--disable", action="store_true")
    dp.add_argument("--connection",
                    help="id of a stored connection holding this provider's "
                         "credential. Without one every lookup is unauthenticated")
    dp.add_argument("--mapping",
                    help="path to a DataProvider document. With one, this "
                         "registration is the whole integration and no connector "
                         "has to be written")

    enrich = sub.add_parser(
        "enrich", help="buy a missing field for entities that lack it")
    enrich.add_argument("--tenant", required=True)
    enrich.add_argument("--field", required=True,
                        choices=["email", "phone", "firmographics"])
    enrich.add_argument("--basis", default="legitimate_interest",
                        choices=["legitimate_interest", "consent", "contract"],
                        help="why this lookup is lawful; recorded against every "
                             "value bought and not reconstructable afterwards")
    enrich.add_argument("--limit", type=int, default=50,
                        help="how many entities to buy for. Bounded on purpose: "
                             "this spends real money on the first run")
    enrich.add_argument("--dry-run", action="store_true",
                        help="report what would be bought and buy nothing")

    dossier = sub.add_parser(
        "research", help="write a research dossier for accounts that lack one")
    dossier.add_argument("--tenant", required=True)
    dossier.add_argument("--account", action="append",
                         help="a specific account id; repeatable. Without any, the "
                              "accounts with no current dossier are taken in order")
    dossier.add_argument("--limit", type=int, default=10,
                         help="how many to write. Bounded low on purpose: at 20 "
                              "credits each this is the most expensive thing here")
    dossier.add_argument("--force", action="store_true",
                         help="rewrite a dossier that is still current, and pay again")
    dossier.add_argument("--dry-run", action="store_true",
                         help="report what would be written and write nothing")

    look = sub.add_parser(
        "watch", help="look for the signals live programs are waiting on")
    look.add_argument("--tenant", required=True)
    look.add_argument("--signal", help="only this one")
    look.add_argument("--limit", type=int, default=500,
                      help="accounts examined per signal. A check is billed once "
                           "per account per day however many signals ask")

    lat = sub.add_parser("latency",
                         help="time to touch, split into detection and execution")
    lat.add_argument("--tenant", required=True)

    hits = sub.add_parser("hit-rates",
                          help="the measured per-provider per-cohort matrix")
    hits.add_argument("--tenant", required=True)
    hits.add_argument("--field", choices=["email", "phone", "firmographics"])

    caps = sub.add_parser("capacity", help="what the sending fleet can do today")
    caps.add_argument("--tenant", required=True)

    terms = sub.add_parser(
        "set-terms",
        help="change a tenant's plan or raise its credit ceiling (provisioning)")
    terms.add_argument("--tenant", required=True)
    terms.add_argument("--plan", choices=["starter", "growth", "scale", "enterprise"])
    terms.add_argument(
        "--credit-ceiling",
        help="hard spend ceiling in credits, or 'plan' to fall back to the "
             "plan's allowance. Above the plan, the excess bills as overage")
    terms.add_argument("--negotiated", help="path to a JSON file of enterprise terms")

    invite = sub.add_parser("invite", help="mint a signup invitation; token shown once")
    invite.add_argument("--company", required=True)
    invite.add_argument("--email")
    invite.add_argument("--region", default="eu", choices=["eu", "us"])
    invite.add_argument("--blueprint")
    invite.add_argument("--days", type=int, default=14)
    invite.add_argument("--base-url", default="https://app.zolts.com",
                        help="used only to print the link the invitee opens")

    pre = sub.add_parser("preflight",
                         help="check a deployment before it takes traffic")
    pre.add_argument("--json", action="store_true", help="machine-readable output")

    rotate = sub.add_parser(
        "rotate-key",
        help="re-seal every credential under ZOLTS_SECRET_KEY; resumable, idempotent")
    rotate.add_argument("--check", action="store_true",
                        help="report what is outstanding and change nothing")
    rotate.add_argument("--batch", type=int, default=100)
    rotate.add_argument("--json", action="store_true", help="machine-readable output")

    hook = sub.add_parser("webhook", help="create an inbound endpoint; secret shown once")
    hook.add_argument("--tenant", required=True)
    hook.add_argument("--provider", required=True)

    base = sub.add_parser(
        "baseline", help="freeze what a tenant's GTM cost and produced before Zolts")
    base.add_argument("--tenant", required=True)
    base.add_argument("--file", required=True,
                      help="a JSON document with the fields of POST /v1/baseline; "
                           "'-' reads stdin")
    base.add_argument("--signed-by", required=True,
                      help="who signs the letter that quotes the digest")

    serve = sub.add_parser("serve", help="run the API")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    db = _db(settings)

    if args.command == "migrate":
        applied = db.migrate()
        db.grant_app_role()
        print(json.dumps({"applied": applied}))
        if not settings.isolation_enforced:
            print("warning: ZOLTS_APP_DATABASE_URL is unset, so the application connects as"
                  " the owner. Row-level security is FORCED and still applies, but the"
                  " second lock is missing.", file=sys.stderr)
        return 0

    if args.command == "create-tenant":
        row = create_tenant(db, slug=args.slug, name=args.name, region=args.region,
                            blueprint_id=args.blueprint, compliance_tier=args.compliance_tier)
        print(json.dumps({"id": str(row["id"]), "slug": row["slug"]}))
        return 0

    if args.command == "issue-key":
        issued = issue_api_key(db, args.tenant, args.name, args.scope)
        print(json.dumps({"token": issued.token, "prefix": issued.prefix,
                          "note": "store this now; it is not recoverable"}))
        return 0

    if args.command == "connect":
        secret = sys.stdin.read().strip()
        if not secret:
            print("no secret on stdin", file=sys.stderr)
            return 2
        connection_id = store_connection(
            db, args.tenant, provider=args.provider, secret=secret,
            secret_key=settings.keyring, display_name=args.display_name,
            config=json.loads(args.config))
        print(json.dumps({"connection_id": connection_id, "provider": args.provider}))
        return 0

    if args.command == "quickstart":
        print(json.dumps(quickstart(
            db, secret_key=settings.keyring, slug=args.slug, name=args.name,
            region=args.region, blueprint=args.blueprint, base_url=args.base_url), indent=2))
        return 0

    if args.command == "sync":
        from runtime.connectors.crm import sources
        from runtime.connectors.generic import GenericSource
        from runtime.connectors.sync import pull
        from runtime.crypto import open_sealed
        from runtime.repo import mappings

        install_default_connectors()
        source = None
        if args.provider not in sources():
            # Not a connector this repository ships. It may still be a mapping
            # the tenant authored, which is the ordinary case for an in-house
            # CRM and must not read as "unsupported".
            with db.tenant_tx(args.tenant) as cur:
                stored = mappings.get(cur, args.provider)
            if stored is None:
                print(f"no CRM source or mapping for '{args.provider}'; built in: "
                      f"{', '.join(sources())}. Publish a mapping with "
                      f"POST /v1/crm/mappings to connect anything else.",
                      file=sys.stderr)
                return 2
            source = GenericSource(stored["document"])

        with db.tenant_tx(args.tenant) as cur:
            cur.execute("select secret_enc, config from connection where provider = %s"
                        " and status = 'active' limit 1", (args.provider,))
            row = cur.fetchone()
        if row is None:
            print(f"no active {args.provider} connection for this tenant; run 'connect' first",
                  file=sys.stderr)
            return 2

        # Some CRMs are not one host. Salesforce gives each org its own, so the
        # credential alone cannot reach it and the connection's config is part
        # of the address. A source that needs configuring says so by exposing
        # `from_config`; the rest are untouched.
        if source is None:
            from runtime.connectors.base import PermanentError
            from runtime.connectors.crm import get_source

            builder = getattr(type(get_source(args.provider)), "from_config", None)
            if builder is not None:
                try:
                    source = builder(row["config"] or {})
                except PermanentError as exc:
                    print(str(exc), file=sys.stderr)
                    return 2
        report = pull(db, args.tenant,
                      open_sealed(row["secret_enc"], settings.keyring),
                      provider=args.provider, source=source, batch_size=args.limit)
        print(json.dumps(dataclasses.asdict(report), indent=2))
        return 0

    if args.command == "crm-mapping":
        from runtime.repo import mappings
        from zolts.mapping import load

        from runtime.connectors.crm import sources

        install_default_connectors()
        document = load(args.file)
        provider = document["metadata"]["provider"]
        if provider in sources():
            print(f"'{provider}' is a connector this runtime ships; a mapping cannot "
                  f"claim its name — choose another", file=sys.stderr)
            return 2
        reads_opt_out = bool(document["spec"]["contacts"].get("consent"))
        with db.tenant_tx(args.tenant) as cur:
            row = mappings.publish(cur, args.tenant, document,
                                   reads_opt_out=reads_opt_out, created_by="cli")
        result = {"provider": row["provider"], "spec_hash": row["spec_hash"],
                  "reads_opt_out": reads_opt_out}
        if not reads_opt_out:
            result["warning"] = ("no consent field declared: every contact this "
                                 "mapping imports is unreachable until a basis is "
                                 "established elsewhere")
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "close-period":
        from runtime import metering

        with db.tenant_tx(args.tenant) as cur:
            cur.execute("select * from tenant where id = %s", (args.tenant,))
            tenant = cur.fetchone()
            if tenant is None:
                print(f"no tenant {args.tenant}", file=sys.stderr)
                return 2
            period = metering.open_period(cur, tenant)
            closed = metering.close_period(cur, tenant, str(period["id"]))
        print(json.dumps({"period": str(closed["id"]),
                          "from": closed["starts_at"].isoformat(),
                          "to": closed["ends_at"].isoformat(),
                          "statement": closed["statement"]}, indent=2))
        return 0

    if args.command == "set-terms":
        # Deliberately not an API route. Raising a spend ceiling is the act
        # that lets a tenant be charged more than they were sold, and the role
        # that serves requests cannot write to `tenant` at all — which is the
        # isolation design, and the reason this is a command an operator runs.
        from decimal import Decimal

        from runtime import metering
        from zolts.billing import BillingError

        fields, values = [], []
        if args.plan:
            fields.append("plan = %s")
            values.append(args.plan)
        if args.negotiated:
            fields.append("negotiated_terms = %s")
            values.append(Path(args.negotiated).read_text())
        if args.credit_ceiling is not None:
            fields.append("credit_ceiling = %s")
            values.append(None if args.credit_ceiling == "plan"
                          else Decimal(args.credit_ceiling))
        if not fields:
            print("nothing to change; pass --plan, --credit-ceiling or --negotiated",
                  file=sys.stderr)
            return 2

        with db.admin_tx() as cur:
            cur.execute(f"update tenant set {', '.join(fields)} where id = %s"
                        " returning *", (*values, args.tenant))
            tenant = one(cur)
            if tenant is None:
                print(f"no tenant {args.tenant}", file=sys.stderr)
                return 2
            # Reject terms that cannot be billed here rather than at the
            # invoice: the transaction rolls back and nothing is half-applied.
            try:
                metering._terms(tenant)
            except BillingError as exc:
                raise SystemExit(f"these terms cannot be billed: {exc}")

        ceiling = tenant["credit_ceiling"]
        print(json.dumps({
            "tenant": str(tenant["id"]), "plan": tenant["plan"],
            "creditCeiling": None if ceiling is None else float(ceiling),
            "note": ("the ceiling is the plan's allowance" if ceiling is None else
                     "credits consumed above the plan bill as overage")}, indent=2))
        return 0

    if args.command == "sending-domain":
        with db.tenant_tx(args.tenant) as cur:
            cur.execute(
                "insert into sending_domain (tenant_id, name, spf, dkim,"
                " dmarc_policy, one_click_unsubscribe) values (%s,%s,%s,%s,%s,%s)"
                " on conflict (tenant_id, name) do update set"
                "   spf = excluded.spf, dkim = excluded.dkim,"
                "   dmarc_policy = excluded.dmarc_policy,"
                "   one_click_unsubscribe = excluded.one_click_unsubscribe"
                " returning *",
                (args.tenant, args.name, args.spf, args.dkim, args.dmarc,
                 args.one_click_unsubscribe))
            row = one(cur)
            if args.resume:
                # Lifting a cut-off is a human saying the cause is fixed. The
                # breaker will trip again on the next complaint if it is not.
                cur.execute("update sending_domain set paused = false,"
                            " paused_reason = null, paused_at = null"
                            " where id = %s returning *", (row["id"],))
                row = one(cur)
        issues = []
        if not row["spf"]:
            issues.append("spf.missing")
        if not row["dkim"]:
            issues.append("dkim.missing")
        if row["dmarc_policy"] not in ("quarantine", "reject"):
            issues.append("dmarc.too_weak")
        if not row["one_click_unsubscribe"]:
            issues.append("list_unsubscribe.missing")
        print(json.dumps({
            "domain": row["name"], "paused": row["paused"],
            "authenticationIssues": issues,
            "note": ("this domain has no capacity until every issue is resolved; "
                     "missing authentication is mail filtered on arrival, not a "
                     "reputation problem to recover from") if issues else
                    "authenticated"}, indent=2))
        return 0

    if args.command == "mailbox":
        from datetime import date

        started = date.fromisoformat(args.warmup_started) if args.warmup_started else None
        with db.tenant_tx(args.tenant) as cur:
            cur.execute(
                "insert into mailbox (tenant_id, address, domain, provider,"
                " warmup_started_on) values (%s,%s,%s,%s, coalesce(%s, current_date))"
                " on conflict (tenant_id, address) do update set"
                "   provider = excluded.provider,"
                "   warmup_started_on = coalesce(%s, mailbox.warmup_started_on)"
                " returning *",
                (args.tenant, args.address, args.address.split("@")[-1],
                 args.provider, started, started))
            row = one(cur)
            if args.pause or args.resume:
                cur.execute("update mailbox set paused = %s, paused_reason = %s"
                            " where id = %s returning *",
                            (bool(args.pause), "paused by an operator" if args.pause
                             else None, row["id"]))
                row = one(cur)
            cur.execute("select 1 from sending_domain where name = %s", (row["domain"],))
            registered = cur.fetchone() is not None
        print(json.dumps({
            "mailbox": row["address"], "provider": row["provider"],
            "warmupStartedOn": row["warmup_started_on"].isoformat(),
            "paused": row["paused"],
            "note": None if registered else
                    f"the domain {row['domain']} is not registered, so this "
                    f"mailbox has no capacity until it is"}, indent=2))
        return 0

    if args.command == "data-provider":
        mapping = None
        if args.mapping:
            import yaml

            from runtime.connectors.declarative_provider import (ProviderDocumentError,
                                                                 validate)
            try:
                mapping = validate(yaml.safe_load(Path(args.mapping).read_text()))
            except ProviderDocumentError as exc:
                # Refused on the way in: the first lookup happens against a real
                # endpoint with a real credential.
                print(f"the provider document is not usable: {exc}", file=sys.stderr)
                return 2

        fields = [f.strip() for f in args.fields.split(",") if f.strip()]
        unknown = set(fields) - set(dataprovider_fields())
        if unknown:
            print(f"unpriced fields: {', '.join(sorted(unknown))}; known: "
                  f"{', '.join(dataprovider_fields())}", file=sys.stderr)
            return 2
        with db.tenant_tx(args.tenant) as cur:
            cur.execute(
                "insert into data_provider (tenant_id, key, fields, unit_cost_micros,"
                " accuracy, default_hit_rate, billed_on_miss, enabled, config,"
                " connection_id) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " on conflict (tenant_id, key) do update set"
                "   fields = excluded.fields,"
                "   unit_cost_micros = excluded.unit_cost_micros,"
                "   accuracy = excluded.accuracy,"
                "   default_hit_rate = excluded.default_hit_rate,"
                "   billed_on_miss = excluded.billed_on_miss,"
                "   enabled = excluded.enabled,"
                "   config = excluded.config,"
                "   connection_id = coalesce(excluded.connection_id,"
                "                            data_provider.connection_id) returning *",
                (args.tenant, args.key, fields, args.cost_micros, args.accuracy,
                 args.hit_rate, args.billed_on_miss, not args.disable,
                 json.dumps({"mapping": mapping} if mapping else {}),
                 args.connection))
            row = one(cur)
        registered = bool(mapping) or args.key in providers_registered()
        print(json.dumps({
            "provider": row["key"], "fields": list(row["fields"]),
            "unitCostEur": int(row["unit_cost_micros"]) / 1_000_000,
            "enabled": row["enabled"],
            "authenticated": row["connection_id"] is not None,
            "note": None if registered else
                    f"no connector named '{args.key}' is registered in this "
                    f"process, so every lookup through it will error until one is"},
            indent=2))
        return 0

    if args.command == "enrich":
        from runtime import enrichment

        table = "account" if args.field == "firmographics" else "person"
        missing = {
            "email": "email is null",
            "phone": "phone is null",
            "firmographics": "(employee_band is null or industry_code is null)",
        }[args.field]

        with db.tenant_tx(args.tenant) as cur:
            cur.execute(f"select * from {table} where {missing}"
                        f" order by created_at limit %s", (args.limit,))
            rows = [dict(r) for r in cur.fetchall()]
            if args.dry_run:
                print(json.dumps({
                    "field": args.field, "candidates": len(rows), "bought": 0,
                    "note": "dry run: nothing was asked and nothing was paid"},
                    indent=2))
                return 0

            enrichment.install_declared(cur)
            cur.execute("select * from tenant where id = %s", (args.tenant,))
            tenant = one(cur)
            results = []
            for row in rows:
                account = None
                if table == "person":
                    # The cohort is the account's, not the person's: hit rates
                    # move with country and company size.
                    cur.execute(
                        "select a.* from account a join membership m"
                        " on m.account_id = a.id where m.person_id = %s limit 1",
                        (row["id"],))
                    found = cur.fetchone()
                    account = dict(found) if found else None
                results.append(enrichment.resolve(
                    cur, tenant, field_name=args.field, entity=row, account=account,
                    legal_basis=args.basis, secret_key=settings.keyring))

        hits_found = [r for r in results if r.hit]
        spent = sum(r.cost_micros for r in results)
        billed = sum(r.credits for r in hits_found)
        print(json.dumps({
            "field": args.field,
            "candidates": len(rows),
            "resolved": len(hits_found),
            "hitRate": round(len(hits_found) / len(rows), 3) if rows else None,
            "spendEur": round(spent / 1_000_000, 4),
            "creditsBilled": billed,
            "byProvider": {k: sum(1 for r in hits_found if r.provider == k)
                           for k in sorted({r.provider for r in hits_found if r.provider})},
            "note": "misses are paid for and not billed to the tenant",
        }, indent=2))
        return 0

    if args.command == "research":
        from runtime import research

        with db.tenant_tx(args.tenant) as cur:
            if args.account:
                cur.execute("select * from account where id = any(%s)", (args.account,))
            else:
                # Accounts with no dossier at all come first. A stale one is
                # still an answer; no answer is not.
                cur.execute(
                    "select a.* from account a"
                    " where not exists (select 1 from dossier d where d.account_id = a.id)"
                    " order by a.created_at limit %s", (args.limit,))
            rows = [dict(r) for r in cur.fetchall()]
            if args.dry_run:
                print(json.dumps({
                    "candidates": len(rows), "written": 0,
                    "wouldCost": len(rows) * 20,
                    "note": "dry run: nothing was written and nothing was billed"},
                    indent=2))
                return 0

            client = guard = None
            if os.environ.get("ZOLTS_AGENTS", "false").lower() == "true":
                from runtime.agents.client import ModelClient
                from runtime.agents.spend import SpendGuard

                client, guard = ModelClient(), SpendGuard()

            cur.execute("select * from tenant where id = %s", (args.tenant,))
            tenant = one(cur)
            results = [research.build(cur, tenant, row, client=client, guard=guard,
                                      force=args.force) for row in rows]

        written = [r for r in results if not r.reused and r.state != "refused"]
        refused = [r for r in results if r.state == "refused"]
        print(json.dumps({
            "candidates": len(rows),
            "written": len(written),
            "reused": sum(1 for r in results if r.reused),
            "refused": len(refused),
            "creditsBilled": round(sum(r.credits for r in results), 4),
            "results": [r.as_dict() for r in results],
            "note": "a dossier is reused while nothing new has happened on the "
                    "account, and a refusal is never billed",
        }, indent=2))
        # A run that produced nothing is a failure an operator must see, and a
        # zero exit on it is how a cron job reports success forever.
        return 1 if rows and not written and not any(r.reused for r in results) else 0

    if args.command == "watch":
        from runtime import watch as watcher

        with db.tenant_tx(args.tenant) as cur:
            cur.execute("select * from tenant where id = %s", (args.tenant,))
            tenant = one(cur)
            if tenant is None:
                print(f"no tenant {args.tenant}", file=sys.stderr)
                return 2
            result = watcher.once(cur, tenant, secret_key=settings.keyring,
                                  limit=args.limit, only=args.signal)
        print(json.dumps(result.as_dict(), indent=2))
        # A pass where every source failed is not a quiet week.
        return 1 if result.signals and all(w.error for w in result.signals) else 0

    if args.command == "latency":
        from runtime import watch as watcher

        with db.tenant_tx(args.tenant) as cur:
            measured = watcher.latency(cur)
        print(json.dumps({
            **measured,
            "note": "docs/06 defines time to touch as signal to action. "
                    "Detection is how long the source took to notice; execution "
                    "is how long this runtime took to act. A bad total is one "
                    "or the other, and they need opposite fixes"}, indent=2))
        return 0

    if args.command == "hit-rates":
        from runtime import enrichment

        with db.tenant_tx(args.tenant) as cur:
            rows = enrichment.matrix(cur, args.field)
        print(json.dumps({
            "minObservations": enrichment.MIN_OBSERVATIONS,
            "note": "a cell below the sample floor is reported untrusted: four "
                    "observations and four thousand look identical as a percentage",
            "matrix": rows}, indent=2))
        return 0

    if args.command == "capacity":
        from runtime import fleet

        with db.tenant_tx(args.tenant) as cur:
            print(json.dumps(fleet.health(cur), indent=2))
        return 0

    if args.command == "liveness":
        from runtime import liveness

        report = liveness.check(db)
        print(liveness.render(report))
        return 1 if report.failing else 0

    if args.command == "invite":
        # Minting has no HTTP surface. An operator capability reachable from a
        # tenant's key is a privilege escalation waiting to be found.
        from runtime import onboarding

        issued = onboarding.mint(db, company_name=args.company, email=args.email,
                                 region=args.region, blueprint_id=args.blueprint,
                                 ttl_days=args.days)
        print(json.dumps({
            "invitation_id": issued.id,
            "token": issued.token,
            "expires_at": issued.expires_at.isoformat(),
            "link": f"{args.base_url.rstrip('/')}/signup/{issued.token}",
            "note": "the token is shown once and is not recoverable",
        }, indent=2))
        return 0

    if args.command == "preflight":
        from runtime import preflight

        report = preflight.run(settings, db)
        print(json.dumps(report.as_dict(), indent=2) if args.json
              else preflight.render(report))
        # A blocking failure is a non-zero exit, so a release command or a
        # deploy script stops rather than serving a misconfigured runtime.
        return 1 if report.blocking else 0

    if args.command == "rotate-key":
        from runtime import rotation

        before = rotation.outstanding(db, settings.keyring)
        if args.check:
            payload = {"outstanding": before.__dict__, "complete": before.complete}
            print(json.dumps(payload, indent=2) if args.json
                  else _render_rotation(before, None))
            # Not an error: an operator asking "is it done" gets an answer, and
            # the answer being "no" is what they asked about.
            return 0

        if not settings.previous_secret_keys and not before.complete:
            # The rows are sealed under something this deployment does not
            # hold. Running would rewrite nothing and report failures; saying
            # so is the useful answer.
            print("::error::rows are sealed under a key this deployment does not hold, "
                  "and ZOLTS_PREVIOUS_SECRET_KEYS is empty. Set the old key there "
                  "before rotating, or the credentials cannot be re-sealed.",
                  file=sys.stderr)
            return 1

        done = rotation.rotate(db, settings.keyring, batch_size=args.batch)
        after = rotation.outstanding(db, settings.keyring)
        payload = {"rotated": done.__dict__, "outstanding": after.__dict__,
                   "complete": after.complete}
        print(json.dumps(payload, indent=2) if args.json
              else _render_rotation(after, done))
        # A credential nothing could open is a real failure and the exit code
        # says so, but only after the other credentials were re-sealed.
        return 1 if done.failed else 0

    if args.command == "baseline":
        from runtime.repo import baseline as baseline_repo
        from zolts.baseline import BaselineError, from_mapping

        raw = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        try:
            captured = from_mapping(json.loads(raw))
        except (BaselineError, ValueError) as exc:
            print(f"the baseline cannot be frozen as written: {exc}", file=sys.stderr)
            return 2
        with db.tenant_tx(args.tenant) as cur:
            try:
                row = baseline_repo.freeze(cur, args.tenant, captured,
                                           signed_by=args.signed_by, captured_by="operator")
            except baseline_repo.BaselineAlreadyFrozen as exc:
                print(str(exc), file=sys.stderr)
                return 1
            ledger.audit(cur, args.tenant, actor="operator", action="baseline.frozen",
                         subject=args.tenant,
                         detail={"digest": row["digest"], "signed_by": args.signed_by,
                                 "source": row["source"]})
        print(json.dumps({**baseline_repo.as_dict(row),
                          "note": "quote the digest in the letter the partner signs; the "
                                  "row and the letter can then be checked against each "
                                  "other by anyone holding both"}, indent=2, default=str))
        return 0

    if args.command == "webhook":
        created = create_webhook_endpoint(db, args.tenant, provider=args.provider,
                                          secret_key=settings.keyring)
        print(json.dumps(created))
        return 0

    if args.command == "worker":
        # Built the same way the cron-invoked tick builds it, so a deployment
        # runs one worker whichever host runs it.
        from runtime.engine.worker import from_settings

        runner, warnings = from_settings(db, settings)
        for warning in warnings:
            print(f"warning: {warning}.", file=sys.stderr)
        if args.once:
            print(json.dumps(runner.tick().__dict__, default=str))
            return 0
        runner.run(interval=args.interval)
        return 0

    if args.command == "serve":
        import uvicorn

        from runtime.api.app import create_app

        uvicorn.run(create_app(db, secret_key=settings.keyring),
                    host=args.host, port=args.port)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
