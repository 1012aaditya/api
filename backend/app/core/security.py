"""Credential handling: passwords, API keys, session tokens (§17, §23).

Two different secrets, two different treatments:

* A **password** is low-entropy and user-chosen, so it gets bcrypt — a
  deliberately slow KDF with a per-password salt.
* An **API key** is 32 bytes from ``secrets.token_urlsafe``. Brute-forcing it
  is infeasible regardless of the digest, and lookup has to be a single
  indexed query, so it gets SHA-256. Running bcrypt here would force a scan
  over every key row on every request without adding real protection.

Neither plaintext is ever stored.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import secrets
from typing import Any, Literal

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.errors import InvalidRequestError

_BCRYPT_ROUNDS = 12
_KEY_SECRET_BYTES = 32
_DISPLAY_PREFIX_CHARS = 6


# --- Passwords ---------------------------------------------------------


def _prepare_password(password: str) -> bytes:
    """Fold the password to a fixed 44 bytes.

    bcrypt silently truncates input at 72 bytes, which would make two long
    passwords sharing a prefix interchangeable. Hashing first removes the
    limit; base64 keeps the result free of NUL bytes, which bcrypt also
    truncates on.
    """
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise InvalidRequestError("Password must be at least 10 characters long.")
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(_prepare_password(password), salt).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare_password(password), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


# --- API keys ----------------------------------------------------------


class GeneratedAPIKey:
    """A freshly minted key. ``plaintext`` is returned to the caller once."""

    __slots__ = ("plaintext", "key_hash", "prefix", "last_four")

    def __init__(self, plaintext: str, key_hash: str, prefix: str, last_four: str) -> None:
        self.plaintext = plaintext
        self.key_hash = key_hash
        self.prefix = prefix
        self.last_four = last_four

    def masked(self) -> str:
        return f"{self.prefix}...{self.last_four}"


def generate_api_key(environment: Literal["live", "test"] = "live") -> GeneratedAPIKey:
    secret = secrets.token_urlsafe(_KEY_SECRET_BYTES)
    plaintext = f"dp_{environment}_{secret}"
    return GeneratedAPIKey(
        plaintext=plaintext,
        key_hash=hash_api_key(plaintext),
        prefix=f"dp_{environment}_{secret[:_DISPLAY_PREFIX_CHARS]}",
        last_four=secret[-4:],
    )


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def parse_api_key_environment(plaintext: str) -> str | None:
    """Read the environment out of a key without trusting it for authorization."""
    parts = plaintext.split("_", 2)
    if len(parts) != 3 or parts[0] != "dp" or parts[1] not in {"live", "test"}:
        return None
    return parts[1]


def looks_like_api_key(candidate: str) -> bool:
    return parse_api_key_environment(candidate) is not None


# --- Session tokens (dashboard) ----------------------------------------


def create_access_token(
    *, user_id: str, organization_id: str, expires_minutes: int | None = None
) -> str:
    settings = get_settings()
    now = dt.datetime.now(dt.UTC)
    expires = now + dt.timedelta(minutes=expires_minutes or settings.jwt_expire_minutes)
    payload = {
        "sub": user_id,
        "org": organization_id,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "access":
        return None
    return payload


# --- Webhook signing (used by the webhook sender in a later phase) ------


def sign_payload(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_signature(payload: bytes, secret: str, signature: str) -> bool:
    return hmac.compare_digest(sign_payload(payload, secret), signature)
