"""Platform-only TOTP seed encryption; no shared company/provider keys."""
import base64
import secrets
import time
from uuid import UUID

from cryptography.hazmat.primitives.hashes import SHA1
from cryptography.hazmat.primitives.twofactor import InvalidToken
from cryptography.hazmat.primitives.twofactor.totp import TOTP

from sales_backend.security.runtime_credentials import CredentialCipher, RuntimeCredentialUnavailable


class PlatformMFACipher(CredentialCipher):
    @staticmethod
    def aad(operator_id: str) -> bytes:
        return f'{UUID(operator_id)}:platform-totp:aes256gcm-v1'.encode()

    def seal(self, operator_id: str, seed: bytes) -> dict:
        result = self.encrypt(operator_id, base64.b32encode(seed).decode())
        return {'version': 1, 'key_id': self.key_id,
                'ciphertext': base64.b64encode(result['api_key_ciphertext']).decode()}

    def open(self, operator_id: str, record: dict) -> bytes:
        try:
            if record['version'] != 1:
                raise ValueError()
            seed = base64.b32decode(self.decrypt(operator_id, record['key_id'],
                                   base64.b64decode(record['ciphertext'], validate=True)))
            if len(seed) < 20:
                raise ValueError()
            return seed
        except (KeyError, TypeError, ValueError):
            raise RuntimeCredentialUnavailable() from None


def authenticator(seed: bytes, length: int = 6) -> TOTP:
    return TOTP(seed, length, SHA1(), 30)


def new_seed() -> bytes:
    return secrets.token_bytes(20)


def match_step(seed: bytes, code: str, now: float | None = None) -> int | None:
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        return None
    current = int(time.time() if now is None else now) // 30
    for step in (current, current - 1, current + 1):
        if step < 0:
            continue
        try:
            authenticator(seed).verify(code.encode(), step * 30)
            return step
        except InvalidToken:
            continue
    return None
