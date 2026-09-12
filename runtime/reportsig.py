"""Where the signing key comes from, and what the instance publishes about it.

`zolts.reportsig` signs and verifies with key bytes it is handed. This is the
half that decides which bytes: the seed is derived from the sealing secret
the runtime already holds, so a deployment configures one secret and gets
both properties (decision 58). `ZOLTS_REPORT_SIGNING_KEY`, a base64 seed,
overrides the derivation for a deployment that wants the two keys to have
different lives.

**Rotation follows the sealing keyring.** A previous sealing secret still
opens a credential (ADR for SEC-1); it still names a public key too, so an
export signed before the rotation verifies against a key the instance still
publishes as *not current*. Retiring the secret from the ring retires the
key, and a document signed under it is then a document its holder verifies
with the key they pinned when they received it — which is what pinning is for.

**Signed on export, never stored.** Ed25519 is deterministic: the same key
over the same digest and prose yields the same signature every time, so
nothing is gained by storing it and something is lost — a row the serving
role cannot update (ADR-043) would need a second table to carry a signature
made after freezing. The body is immutable; the signature is a function of it.
"""

from __future__ import annotations

import base64
import os
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from runtime.crypto import Keyring
from zolts import reportsig
from zolts.reportsig import SEED_BYTES, Signer, SigningError

INFO = b"zolts.report-signing.v1"
OVERRIDE = "ZOLTS_REPORT_SIGNING_KEY"
KEYS_PATH = "/.well-known/zolts-signing-keys.json"


def seed_from_secret(secret: str) -> bytes:
    """HKDF over the sealing secret with a fixed purpose string, so the
    signing seed and the AES key `runtime.crypto` derives from the same
    secret are unrelated bytes: learning one says nothing about the other."""
    return HKDF(algorithm=hashes.SHA256(), length=SEED_BYTES, salt=None,
                info=INFO).derive(secret.encode("utf-8"))


def _override_seed() -> bytes | None:
    configured = os.environ.get(OVERRIDE)
    if not configured:
        return None
    try:
        seed = base64.b64decode(configured, validate=True)
    except (ValueError, TypeError) as exc:
        raise SigningError(f"{OVERRIDE} is not base64: {exc}") from exc
    if len(seed) != SEED_BYTES:
        raise SigningError(f"{OVERRIDE} decodes to {len(seed)} bytes; a seed is {SEED_BYTES}")
    return seed


def signer_for(secret: "str | Keyring") -> Signer:
    """The key that signs today."""
    override = _override_seed()
    if override is not None:
        return Signer.from_seed(override)
    return Signer.from_seed(seed_from_secret(Keyring.of(secret).primary))


def published_keys(secret: "str | Keyring") -> dict[str, Any]:
    """What the instance says about its keys: the current one first, then
    every key a previous secret still names. Public keys are public; this
    is served without authentication."""
    ring = Keyring.of(secret)
    current = signer_for(ring)
    keys = [reportsig.public_key_entry(current.public_key, current=True)]
    for previous in ring.previous:
        older = Signer.from_seed(seed_from_secret(previous))
        if older.key_id != current.key_id:
            keys.append(reportsig.public_key_entry(older.public_key, current=False))
    return {"kind": "zolts.signing-keys.v1", "algorithm": reportsig.ALGORITHM, "keys": keys}


def export(row: dict[str, Any], program: dict[str, Any], secret: "str | Keyring",
           *, keys_url: str | None = None) -> dict[str, Any]:
    """The signed document for one frozen report row."""
    return reportsig.document(
        body=row["body"], rendered=row["rendered"],
        program_key=program["key"], program_version=program["version"],
        period_start=row["period_start"].isoformat(),
        period_end=row["period_end"].isoformat(),
        verdict=row["verdict"],
        frozen_at=row["frozen_at"].isoformat(), frozen_by=row["frozen_by"],
        signer=signer_for(secret), keys_url=keys_url)
