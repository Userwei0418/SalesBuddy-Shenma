"""Password encoding shared by account provisioning and authentication."""

import base64
import hashlib
import hmac
import secrets

N, R, P = 32768, 8, 1


def validate_password(password: str) -> None:
    if not 8 <= len(password) <= 128:
        raise ValueError("密码需为 8–128 个字符")


def encode_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, maxmem=64 * 1024 * 1024)
    return f"scrypt${N}${R}${P}${base64.b64encode(salt).decode()}${base64.b64encode(key).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if algorithm != "scrypt" or (int(n), int(r), int(p)) != (N, R, P) or len(password) > 128:
            return False
        key = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt, validate=True), n=N, r=R, p=P, maxmem=64 * 1024 * 1024
        )
        return hmac.compare_digest(key, base64.b64decode(expected, validate=True))
    except (ValueError, TypeError):
        return False


# Equal-cost verification for unknown accounts; contains no usable credential.
DUMMY_HASH = "scrypt$32768$8$1$MDEyMzQ1Njc4OWFiY2RlZg==$" + base64.b64encode(bytes(64)).decode()
