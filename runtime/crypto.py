"""Secret sealing for connector credentials.

Credentials are AES-GCM sealed with a key held outside the database. A database
dump therefore discloses nothing on its own, which is the property that matters
when the database is managed by someone else.

**One key seals every credential of every tenant**, which makes rotating it a
security control rather than housekeeping: a key that leaks and cannot be
replaced is a permanent compromise of every customer's CRM. Rotation needs two
things this module provides — a window in which both the new and the old key
open a blob, and a name for the key that sealed each row so that *is the
rotation finished* is a count rather than a guess.

The key id is a hash of the key's own hash, truncated. It names a key without
disclosing anything about it, which is what lets it sit unencrypted in a
column beside the ciphertext.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12
_KEY_ID_CHARS = 12


class CannotOpen(ValueError):
    """No configured key opens this blob."""


def _key(secret: str) -> bytes:
    # The configured secret is arbitrary text; the cipher needs 32 bytes.
    return hashlib.sha256(secret.encode()).digest()


def key_id(secret: str) -> str:
    """A public name for a key: the hash of the key material, truncated.

    Stored in the clear next to the ciphertext. It discloses nothing — it is a
    second preimage-resistant hash of a hash — and it turns "which rows are
    still on the old key" into a `where` clause instead of a decryption pass
    over the whole table.
    """
    return hashlib.sha256(_key(secret)).hexdigest()[:_KEY_ID_CHARS]


@dataclass(frozen=True)
class Keyring:
    """The key that seals, and the keys that still open.

    A rotation is not instant: between "the new key is configured" and "every
    row has been re-sealed" the runtime has to serve requests, so it must open
    blobs under the old key while writing new ones under the new. `previous`
    is exactly that window, and it is meant to be emptied once
    `zolts rotate-key` reports nothing outstanding.
    """
    primary: str
    previous: tuple[str, ...] = ()

    @classmethod
    def of(cls, secret: "str | Keyring") -> "Keyring":
        """Accept either, so a caller threading a plain key needs no change."""
        return secret if isinstance(secret, cls) else cls(primary=str(secret))

    @property
    def primary_id(self) -> str:
        return key_id(self.primary)

    @property
    def known_ids(self) -> tuple[str, ...]:
        return tuple(key_id(k) for k in (self.primary, *self.previous))

    def opens(self, stored_key_id: str | None) -> bool:
        """Can this keyring open a row sealed under that key id?

        A null id is a row written before key ids existed. It is not treated
        as openable on faith: the caller has to try, and the answer is whether
        the trial succeeded.
        """
        return stored_key_id in self.known_ids if stored_key_id else False


# What every function that threads the key accepts: the key itself, or the
# ring that holds it plus the ones a rotation has not finished retiring.
SecretKey = "str | Keyring"


def seal(plaintext: str, secret: "str | Keyring") -> bytes:
    """Seal under the primary key. Previous keys open; they never write."""
    ring = Keyring.of(secret)
    nonce = os.urandom(_NONCE_BYTES)
    return nonce + AESGCM(_key(ring.primary)).encrypt(nonce, plaintext.encode(), None)


def open_sealed(blob: bytes, secret: "str | Keyring") -> str:
    """Open with the primary key, then with each previous key.

    Trying keys in turn is safe rather than sloppy: AES-GCM authenticates, so
    a wrong key fails the tag check instead of returning plausible bytes. The
    cost of a miss is one failed decryption of a few dozen bytes.
    """
    ring = Keyring.of(secret)
    blob = bytes(blob)
    nonce, cipher_body = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
    for candidate in (ring.primary, *ring.previous):
        try:
            return AESGCM(_key(candidate)).decrypt(nonce, cipher_body, None).decode()
        except Exception:  # noqa: BLE001 - the library raises InvalidTag and friends
            continue
    raise CannotOpen(
        "no configured key opens this credential; the keyring holds "
        f"{len(ring.previous) + 1} key(s) ({', '.join(ring.known_ids)}). "
        "Either ZOLTS_SECRET_KEY changed without a rotation, or the key that "
        "sealed it is gone — in which case the credential must be re-entered, "
        "because nothing in this database can recover it.")


# -- API tokens ----------------------------------------------------------

TOKEN_PREFIX = "zk_"


def new_api_token() -> tuple[str, str, str]:
    """Return (token, sha256 hash, display prefix). The token is never stored."""
    body = secrets.token_urlsafe(32)
    token = f"{TOKEN_PREFIX}{body}"
    return token, hash_token(token), token[: len(TOKEN_PREFIX) + 6]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


