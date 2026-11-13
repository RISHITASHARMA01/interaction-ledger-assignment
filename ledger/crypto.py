"""PII protection at rest.

Two derived keys from one master secret:

* a **blind index** key: HMAC-SHA256(phone) gives a deterministic, non-reversible token so events can
  be joined to leads by phone with an ordinary indexed equality lookup, without storing the phone;
* an **encryption** key: Fernet (AES-128-CBC + HMAC-SHA256, random IV) for the PII values
  themselves, so an authorised process can still recover them (e.g. to actually dial).

The master secret comes from ``LEDGER_PII_KEY`` (urlsafe base64 of 32 random bytes). For local runs,
if the variable is unset, a key file is created next to the database with 0600 permissions and
is git-ignored. In production the secret would live in a KMS / secrets manager, never next to the DB.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

ENV_VAR = "LEDGER_PII_KEY"


class Crypto:
    def __init__(self, master: bytes):
        if len(master) < 32:
            raise ValueError("PII master key must be at least 32 bytes")
        self._index_key = hmac.new(master, b"ledger/blind-index/v1", hashlib.sha256).digest()
        enc_key = hmac.new(master, b"ledger/encryption/v1", hashlib.sha256).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(enc_key))
        # Stored in the DB so a run with the wrong key fails loudly instead of silently
        # producing hashes that match nothing (every event would become an orphan).
        self.fingerprint = hmac.new(master, b"ledger/fingerprint/v1", hashlib.sha256).hexdigest()[:16]

    # -- blind index ----------------------------------------------------------------------
    def phone_token(self, canonical_phone: str) -> str:
        return hmac.new(self._index_key, canonical_phone.encode(), hashlib.sha256).hexdigest()

    # -- reversible encryption ------------------------------------------------------------
    def encrypt_json(self, obj: dict) -> bytes:
        return self._fernet.encrypt(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode())

    def decrypt_json(self, token: bytes) -> dict:
        return json.loads(self._fernet.decrypt(token))

    # -- key loading ----------------------------------------------------------------------
    @classmethod
    def from_env_or_file(cls, key_file: Path) -> "Crypto":
        env = os.environ.get(ENV_VAR)
        if env:
            return cls(base64.urlsafe_b64decode(env.encode()))
        if not key_file.exists():
            key_file.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
        return cls(base64.urlsafe_b64decode(key_file.read_text().strip().encode()))
