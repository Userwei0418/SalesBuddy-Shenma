"""Versioned provider credentials, independent of user authentication signing keys."""
from __future__ import annotations

import base64
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FORMAT = "aes256gcm-v1"
LEGACY_FORMAT = "pgp-v1"
LEGACY_KEY_ID = "legacy-pgp"


class RuntimeCredentialUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("RUNTIME_CREDENTIAL_UNAVAILABLE")


def read_private_file(path: str) -> bytes:
    try:
        descriptor = os.open(Path(path), os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 65536:
                raise RuntimeCredentialUnavailable()
            return stream.read(65537)
    except (OSError, ValueError):
        raise RuntimeCredentialUnavailable() from None


@dataclass(frozen=True)
class CredentialCipher:
    key_id: str
    keys: dict[str, bytes] = field(repr=False)

    @classmethod
    def from_file(cls, path: str, key_id: str) -> CredentialCipher:
        try:
            raw = json.loads(read_private_file(path))
            keys = {name: base64.b64decode(value, validate=True) for name, value in raw["keys"].items()}
            if any(not isinstance(name, str) or not 1 <= len(name) <= 100 or name == LEGACY_KEY_ID
                   or len(value) != 32 for name, value in keys.items()):
                raise ValueError()
            return cls(key_id, keys)
        except (AttributeError, KeyError, TypeError, ValueError):
            raise RuntimeCredentialUnavailable() from None

    @staticmethod
    def aad(workspace_id: str) -> bytes:
        return f"{UUID(workspace_id)}:runtime-provider-api-key:{FORMAT}".encode()

    def _key(self, key_id: str) -> bytes:
        key = self.keys.get(key_id)
        if key is None or len(key) != 32:
            raise RuntimeCredentialUnavailable()
        return key

    def encrypt(self, workspace_id: str, plaintext: str) -> dict:
        nonce = os.urandom(12)
        ciphertext = nonce + AESGCM(self._key(self.key_id)).encrypt(nonce, plaintext.encode(), self.aad(workspace_id))
        return {"api_key_ciphertext": ciphertext, "cipher_format": FORMAT,
                "encryption_key_id": self.key_id, "api_key_tail": plaintext[-4:]}

    def decrypt(self, workspace_id: str, key_id: str, ciphertext: bytes) -> str:
        try:
            result = AESGCM(self._key(key_id)).decrypt(ciphertext[:12], ciphertext[12:], self.aad(workspace_id))
            value = result.decode()
            if not value:
                raise ValueError()
            return value
        except (InvalidTag, ValueError, UnicodeDecodeError):
            raise RuntimeCredentialUnavailable() from None


async def decrypt_credential(connection, row, settings) -> str | None:
    ciphertext = row.get("api_key_ciphertext")
    if ciphertext is None:
        return None
    if row.get("cipher_format") == FORMAT:
        cipher = CredentialCipher.from_file(settings.config_credential_keyring_file, settings.config_credential_key_id)
        return cipher.decrypt(str(row["workspace_id"]), row["encryption_key_id"], bytes(ciphertext))
    if row.get("cipher_format") == LEGACY_FORMAT and row.get("encryption_key_id") == LEGACY_KEY_ID:
        # An explicit historical secret, never defaults.access_token_secret.
        try:
            key = read_private_file(settings.config_credential_legacy_key_file).decode()
            if not key:
                raise RuntimeCredentialUnavailable()
            value = await connection.fetchval("SELECT public.pgp_sym_decrypt($1::bytea,$2::text)", ciphertext, key)
            if not value:
                raise RuntimeCredentialUnavailable()
            return value
        except Exception:
            # PostgreSQL decryption errors and their parameters must not enter logs.
            raise RuntimeCredentialUnavailable() from None
    raise RuntimeCredentialUnavailable()
