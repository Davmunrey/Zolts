"""The jurisdiction pack a deployment is running, and the ones it has run.

`zolts/policy.py` said packs were data rather than code and the only pack was
a dict in that file, so a regulatory change needed a release and a decision
recorded in March resolved to whatever the code said today (D-53).

Two things this makes true:

* **A pack is published, not deployed.** An operator publishes a document —
  `zolts policy-pack --file` — and the next decision cites it. Not a tenant:
  a tenant tightening a rule is its program's `policy.overrides`, versioned
  with the program (ADR-030), and a surface where a customer can loosen a
  jurisdiction's requirements is not one this product should have.
* **A decision can be reproduced.** Every `policy_decision` carries the
  version and the digest of the pack that produced it, and the body that
  digest hashes is kept for as long as the decisions citing it are.

The shipped pack is installed on migrate, so a fresh database is never
running rules nobody can name.
"""

from __future__ import annotations

import json
from typing import Any

from runtime.db import one, rows
from zolts import policy


class NoActivePack(RuntimeError):
    """Nothing is published, which means nothing may be decided."""


def install_shipped(cur, *, published_by: str = "shipped") -> dict[str, Any]:
    """Store the pack this release ships with, and make it active if none is.

    Idempotent on the digest: a redeploy of the same rules is the same pack.
    A deployment that has published its own pack keeps it — the shipped one is
    stored beside it so an old decision citing it still resolves, but it does
    not take the active seat back on every restart.
    """
    document = policy.to_document(policy.PACK_V1)
    digest = policy.pack_digest(policy.PACK_V1)
    cur.execute("select digest from policy_pack where active")
    active = one(cur)
    cur.execute(
        "insert into policy_pack (version, digest, body, active, published_by)"
        " values (%s,%s,%s,%s,%s) on conflict (digest) do nothing",
        (policy.PACK_V1_VERSION, digest, json.dumps(document), active is None, published_by))
    return get(cur, digest) or {}


def publish(cur, document: dict[str, Any], *, version: str, published_by: str) -> dict[str, Any]:
    """Publish a pack document and make it the one new decisions cite.

    The document is read into rules before it is stored: a pack that cannot be
    parsed is refused here rather than at the first send, and a pack silently
    missing a country is a pack that allows cold email there.
    """
    pack = policy.from_document(document)          # raises PackDocumentError
    digest = policy.pack_digest(pack)
    body = policy.to_document(pack)                # canonical, not as typed
    cur.execute("update policy_pack set active = false where active")
    cur.execute(
        "insert into policy_pack (version, digest, body, active, published_by)"
        " values (%s,%s,%s,true,%s)"
        " on conflict (digest) do update set active = true,"
        "   version = excluded.version, published_by = excluded.published_by"
        " returning *",
        (version, digest, json.dumps(body), published_by))
    return one(cur)


def active(cur) -> dict[str, Any]:
    """The pack new decisions cite, or a refusal.

    Raising rather than falling back to the shipped dict is the point: a
    deployment whose pack row is missing has no rules anybody can name, and
    deciding under rules nobody can name is exactly what this module exists to
    stop.
    """
    cur.execute("select * from policy_pack where active")
    row = one(cur)
    if row is None:
        raise NoActivePack(
            "no policy pack is active; run `zolts migrate` to install the shipped pack "
            "or `zolts policy-pack --file` to publish one. Nothing may be decided until "
            "the rules can be named")
    return row


def get(cur, digest: str) -> dict[str, Any] | None:
    cur.execute("select * from policy_pack where digest = %s", (digest,))
    return one(cur)


def history(cur, limit: int = 20) -> list[dict[str, Any]]:
    cur.execute("select version, digest, active, published_by, published_at"
                " from policy_pack order by published_at desc limit %s", (limit,))
    return rows(cur)


def rules_of(row: dict[str, Any]) -> dict[str, policy.JurisdictionRule]:
    """The stored body, back as rules the engine evaluates."""
    return policy.from_document(row["body"])
