"""Secret sealing for connector credentials.

Credentials are AES-GCM sealed with a key held outside the database. A database
dump therefore discloses nothing on its own, which is the property that matters
when the database is managed by someone else.
"""

from __future__ import annotations

import hashlib
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12


def _key(secret: str) -> bytes:
    # The configured secret is arbitrary text; the cipher needs 32 bytes.
    return hashlib.sha256(secret.encode()).digest()


def seal(plaintext: str, secret: str) -> bytes:
    nonce = os.urandom(_NONCE_BYTES)
    return nonce + AESGCM(_key(secret)).encrypt(nonce, plaintext.encode(), None)


def open_sealed(blob: bytes, secret: str) -> str:
    blob = bytes(blob)
    nonce, body = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
    return AESGCM(_key(secret)).decrypt(nonce, body, None).decode()


# -- API tokens ----------------------------------------------------------

TOKEN_PREFIX = "zk_"


def new_api_token() -> tuple[str, str, str]:
    """Return (token, sha256 hash, display prefix). The token is never stored."""
    body = secrets.token_urlsafe(32)
    token = f"{TOKEN_PREFIX}{body}"
    return token, hash_token(token), token[: len(TOKEN_PREFIX) + 6]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


