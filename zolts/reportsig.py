"""A frozen report, signed by the instance that froze it, verifiable by
anybody holding the public key and nothing else.

ADR-043 froze the incrementality report: written once, a sha256 digest over
its canonical fields, the rendered document stored verbatim. "Signed" meant
the partner signed a letter quoting the digest. What a CFO receives is a
document, and a document with a digest proves only that it is consistent
with itself: anybody can recompute a digest over figures they changed. The
export now carries a signature the instance's key made over the digest and
the rendered text, so a reader on a machine that has never seen the database
can establish three things and say which one failed: the figures rebuild to
the digest they quote, the prose is the prose that was signed, and the key
that signed is the key the instance publishes (`docs/28`, OX-5).

Pure. The key material arrives as bytes; where it comes from is the
runtime's decision (decision 58). Nothing here reads a file or a clock.

**The signature covers the digest and the prose, not the body twice.** The
digest is already the canonical form's fingerprint, versioned so a document
frozen under an older shape still rebuilds (D-72); signing it binds the
figures. The rendered markdown is not in the canonical form — a template
may improve — so its hash is signed beside the digest: a report whose
prose was edited after freezing fails on the second check, not silently.

**The verdict names the check that failed.** A verifier that answers only
*invalid* leaves the reader to guess whether a figure moved, the prose was
edited, or the wrong key was pinned; each of those is a different
conversation.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (Ed25519PrivateKey,
                                                               Ed25519PublicKey)

from zolts.report import from_mapping

KIND = "zolts.report.v1"
ALGORITHM = "ed25519"
SEED_BYTES = 32
_KEY_ID_CHARS = 12


class SigningError(ValueError):
    pass


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as exc:
        raise SigningError(f"not base64: {exc}") from exc


def canonical_bytes(body: Mapping[str, Any]) -> bytes:
    """The bytes the digest hashes, rebuilt in the shape the body's own
    version declares — the same rebuild `zolts.report.from_mapping` makes
    for the letter (D-72)."""
    report = from_mapping(dict(body))
    return json.dumps(report.canonical(), sort_keys=True, separators=(",", ":")).encode()


def digest_of(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(body)).hexdigest()


def rendered_hash(rendered: str) -> str:
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def key_id_of(public_key: bytes) -> str:
    """A public name for a public key: its hash, truncated. Quoted in the
    export and listed beside the key the instance publishes."""
    return hashlib.sha256(public_key).hexdigest()[:_KEY_ID_CHARS]


def message(digest: str, rendered: str) -> bytes:
    """What is signed: the digest of the figures and the hash of the prose,
    in one canonical line. Either changing after the signature was made is
    a different message."""
    return json.dumps({"digest": digest, "rendered_sha256": rendered_hash(rendered)},
                      sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True)
class Signer:
    private: Ed25519PrivateKey

    @classmethod
    def from_seed(cls, seed: bytes) -> "Signer":
        if len(seed) != SEED_BYTES:
            raise SigningError(f"a signing seed is {SEED_BYTES} bytes, not {len(seed)}")
        return cls(Ed25519PrivateKey.from_private_bytes(seed))

    @property
    def public_key(self) -> bytes:
        return self.private.public_key().public_bytes_raw()

    @property
    def key_id(self) -> str:
        return key_id_of(self.public_key)

    def sign(self, digest: str, rendered: str) -> bytes:
        return self.private.sign(message(digest, rendered))


def public_key_entry(public_key: bytes, *, current: bool) -> dict[str, Any]:
    """One row of what an instance publishes about its signing keys."""
    return {"key_id": key_id_of(public_key), "algorithm": ALGORITHM,
            "public_key": _b64(public_key), "current": current}


def document(*, body: Mapping[str, Any], rendered: str, program_key: str,
             program_version: str, period_start: str, period_end: str,
             verdict: str, frozen_at: str, frozen_by: str, signer: Signer,
             keys_url: str | None = None) -> dict[str, Any]:
    """The export: everything a reader needs to verify it, and the key it
    was signed under. The key is carried for convenience and is not what
    the verifier should trust — a document that vouches for its own key
    proves only that some key signed it. Pin the key from `keys_url`."""
    digest = digest_of(body)
    return {
        "kind": KIND,
        "program": {"key": program_key, "version": program_version},
        "period": {"start": period_start, "end": period_end},
        "verdict": verdict,
        "frozen": {"at": frozen_at, "by": frozen_by},
        "digest": digest,
        "report": dict(body),
        "rendered": rendered,
        "signature": {"algorithm": ALGORITHM, "key_id": signer.key_id,
                      "value": _b64(signer.sign(digest, rendered))},
        "public_key": _b64(signer.public_key),
        "keys_url": keys_url,
    }


CHECKS = ("kind", "figures", "digest", "key", "signature")


@dataclass(frozen=True)
class Verdict:
    ok: bool
    digest: str | None
    key_id: str | None
    checks: dict[str, str]
    """Every check in `CHECKS`, in order: "ok" or the reason it failed. A check
    after a failed one is "not reached" rather than a guess."""
    key_source: str
    """Where the public key came from: "pinned" when the caller supplied it,
    "the document's own" when it did not. The second is a weaker verdict and
    the verifier says so."""

    def lines(self) -> list[str]:
        out = [f"{name:<10} {result}" for name, result in self.checks.items()]
        out.append(f"{'key from':<10} {self.key_source}")
        out.append("VERIFIED" if self.ok else "NOT VERIFIED")
        return out


def verify(doc: Mapping[str, Any], public_key: str | bytes | None = None) -> Verdict:
    """Establish that the document is what the instance signed.

    `public_key` is the key the reader pinned — from the instance's published
    keys, or from the letter. Without it the document's own key is used, and
    the verdict says so: that proves the document was not altered after it
    was signed, and nothing about who signed it.
    """
    checks: dict[str, str] = {name: "not reached" for name in CHECKS}
    digest = None
    stated_key_id = None
    pinned = public_key is not None
    key_source = "pinned" if pinned else "the document's own"

    def fail(name: str, why: str) -> Verdict:
        checks[name] = why
        return Verdict(False, digest, stated_key_id, checks, key_source)

    if doc.get("kind") != KIND:
        return fail("kind", f"{doc.get('kind')!r} is not {KIND}")
    checks["kind"] = "ok"

    try:
        recomputed = digest_of(doc.get("report") or {})
    except Exception as exc:  # a body that does not rebuild is the finding
        return fail("figures", f"the report does not rebuild: {exc}")
    checks["figures"] = "ok"

    digest = str(doc.get("digest") or "")
    if recomputed != digest:
        return fail("digest", f"the figures hash to {recomputed[:12]}…, "
                              f"the document quotes {digest[:12]}…")
    checks["digest"] = "ok"

    signature = doc.get("signature") or {}
    stated_key_id = signature.get("key_id")
    raw_key: bytes
    try:
        raw_key = (_unb64(public_key) if isinstance(public_key, str)
                   else public_key if public_key is not None
                   else _unb64(str(doc.get("public_key") or "")))
        Ed25519PublicKey.from_public_bytes(raw_key)
    except (SigningError, ValueError) as exc:
        return fail("key", f"no usable public key: {exc}")
    if key_id_of(raw_key) != stated_key_id:
        return fail("key", f"the key is {key_id_of(raw_key)}, the signature names "
                           f"{stated_key_id}")
    checks["key"] = "ok"

    if signature.get("algorithm") != ALGORITHM:
        return fail("signature", f"{signature.get('algorithm')!r} is not {ALGORITHM}")
    try:
        Ed25519PublicKey.from_public_bytes(raw_key).verify(
            _unb64(str(signature.get("value") or "")),
            message(digest, str(doc.get("rendered") or "")))
    except (InvalidSignature, SigningError) as exc:
        return fail("signature", "the signature does not cover this digest and this "
                                 "prose under this key" + (f" ({exc})" if str(exc) else ""))
    checks["signature"] = "ok"
    return Verdict(True, digest, stated_key_id, checks, key_source)
