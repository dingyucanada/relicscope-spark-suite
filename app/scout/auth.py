from __future__ import annotations

import hashlib
import hmac
import secrets


SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
TOKEN_BYTES = 32


def issue_device_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_device_token(token: str, *, salt: bytes | None = None) -> tuple[str, str]:
    normalized = token.strip()
    if len(normalized) < 32:
        raise ValueError("device token is shorter than 32 characters")
    selected_salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(
        normalized.encode("utf-8"),
        salt=selected_salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=32,
    )
    return selected_salt.hex(), derived.hex()


def verify_device_token(token: str, salt_hex: str, expected_hash_hex: str) -> bool:
    try:
        _, candidate = hash_device_token(token, salt=bytes.fromhex(salt_hex))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected_hash_hex)


def bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise ValueError("missing authorization header")
    scheme, separator, value = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not value.strip():
        raise ValueError("authorization must use Bearer token")
    return value.strip()
