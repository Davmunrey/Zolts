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
import sys
from pathlib import Path
from typing import Any

from runtime.config import Settings
from runtime.connectors import install_default_connectors
from runtime.db import Database
from runtime.engine.worker import Worker
from runtime.provision import (create_tenant, create_webhook_endpoint, issue_api_key,
                               store_connection)
from runtime.repo import programs


def _db(settings: Settings) -> Database:
    return Database(settings.database_url, settings.app_database_url)


def quickstart(db: Database, *, secret_key: str, slug: str, name: str, region: str,
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
            f"open the console:  curl -H 'x-api-key: {key.token}' {base_url}/console",
            "connect a provider: printf %s \"$TOKEN\" | python3 -m runtime.cli connect"
            f" --tenant {tenant_id} --provider smartlead --config '{{\"campaign_id\":\"...\"}}'",
            "run the worker:     python3 -m runtime.cli worker",
        ],
        "note": "the API key and the webhook secret are shown once and are not recoverable",
    }


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

    hook = sub.add_parser("webhook", help="create an inbound endpoint; secret shown once")
    hook.add_argument("--tenant", required=True)
    hook.add_argument("--provider", required=True)

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
            secret_key=settings.secret_key, display_name=args.display_name,
            config=json.loads(args.config))
        print(json.dumps({"connection_id": connection_id, "provider": args.provider}))
        return 0

    if args.command == "quickstart":
        print(json.dumps(quickstart(
            db, secret_key=settings.secret_key, slug=args.slug, name=args.name,
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
            cur.execute("select secret_enc from connection where provider = %s"
                        " and status = 'active' limit 1", (args.provider,))
            row = cur.fetchone()
        if row is None:
            print(f"no active {args.provider} connection for this tenant; run 'connect' first",
                  file=sys.stderr)
            return 2
        report = pull(db, args.tenant,
                      open_sealed(row["secret_enc"], settings.secret_key),
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

    if args.command == "webhook":
        created = create_webhook_endpoint(db, args.tenant, provider=args.provider,
                                          secret_key=settings.secret_key)
        print(json.dumps(created))
        return 0

    if args.command == "worker":
        install_default_connectors()
        # The agent layer is optional and both halves are required together: a
        # model with no spend guard would generate against no ceiling, and a
        # guard with no model has nothing to price.
        model_client = spend_guard = None
        if settings.agents_enabled:
            from runtime.agents.client import ModelClient
            from runtime.agents.spend import SpendGuard

            model_client = ModelClient()
            spend_guard = SpendGuard()
            if not spend_guard.available:
                print(f"warning: the spend guard '{spend_guard._command}' is not on PATH,"
                      " so every generation will be refused. Install it with"
                      " 'npm i -g @trazum/mcp' or unset ZOLTS_AGENTS.", file=sys.stderr)
        runner = Worker(db, secret_key=settings.secret_key,
                        lease_seconds=settings.lease_seconds, batch=settings.worker_batch,
                        dry_run=settings.dry_run, model_client=model_client,
                        spend_guard=spend_guard)
        if args.once:
            print(json.dumps(runner.tick().__dict__, default=str))
            return 0
        runner.run(interval=args.interval)
        return 0

    if args.command == "serve":
        import uvicorn

        from runtime.api.app import create_app

        uvicorn.run(create_app(db, secret_key=settings.secret_key),
                    host=args.host, port=args.port)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
