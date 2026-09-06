"""Operator commands.

Deliberately small. Anything that changes tenant data belongs behind the API,
where it is authenticated and audited; these are the operations that exist
before a tenant does, plus the two long-running processes.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from runtime.config import Settings
from runtime.connectors import install_default_connectors
from runtime.db import Database
from runtime.engine.worker import Worker
from runtime.provision import create_tenant, issue_api_key, store_connection


def _db(settings: Settings) -> Database:
    return Database(settings.database_url, settings.app_database_url)


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

    if args.command == "worker":
        install_default_connectors()
        runner = Worker(db, secret_key=settings.secret_key,
                        lease_seconds=settings.lease_seconds, batch=settings.worker_batch,
                        dry_run=settings.dry_run)
        if args.once:
            print(json.dumps(runner.tick().__dict__, default=str))
            return 0
        runner.run(interval=args.interval)
        return 0

    if args.command == "serve":
        import uvicorn

        from runtime.api.app import create_app

        uvicorn.run(create_app(db), host=args.host, port=args.port)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
