"""Prefixed, sortable public identifiers.

Every object a customer can see in an API response or a log line carries a
prefixed ULID-style id (``req_01J...``). The prefix makes the id
self-describing in support tickets; the leading 48-bit timestamp makes ids
sort chronologically, which keeps database indexes on them well-behaved.
"""

from __future__ import annotations

import os
import time

# Crockford base32: no I, L, O or U, so ids survive being read aloud.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_TIME_CHARS = 10
_RANDOM_CHARS = 16


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def ulid() -> str:
    """A 26-character, lexicographically sortable, random-suffixed id."""
    timestamp = int(time.time() * 1000)
    randomness = int.from_bytes(os.urandom(10), "big")
    return _encode(timestamp, _TIME_CHARS) + _encode(randomness, _RANDOM_CHARS)


def prefixed_id(prefix: str) -> str:
    return f"{prefix}_{ulid()}"


def request_id() -> str:
    return prefixed_id("req")


def document_id() -> str:
    return prefixed_id("doc")


def job_id() -> str:
    return prefixed_id("job")


def extraction_id() -> str:
    return prefixed_id("ext")


def organization_id() -> str:
    return prefixed_id("org")


def user_id() -> str:
    return prefixed_id("usr")


def api_key_id() -> str:
    return prefixed_id("key")


def usage_event_id() -> str:
    return prefixed_id("evt")
