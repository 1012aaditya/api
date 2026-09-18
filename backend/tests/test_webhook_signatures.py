"""Webhook payload signing (§22, §23).

Delivery itself is a later phase; the signing primitive is here now because
the secret handling is what has to be right, and it is cheap to get right
early.
"""

from __future__ import annotations

import json

from app.core.security import sign_payload, verify_signature

SECRET = "whsec_test_only_not_a_real_secret"


def test_a_signature_verifies_against_its_own_payload() -> None:
    payload = json.dumps(
        {"event": "document.completed", "job_id": "job_123", "status": "completed"}
    ).encode()
    assert verify_signature(payload, SECRET, sign_payload(payload, SECRET))


def test_a_tampered_payload_fails_verification() -> None:
    original = b'{"event":"document.completed","document_id":"doc_1"}'
    tampered = b'{"event":"document.completed","document_id":"doc_2"}'
    signature = sign_payload(original, SECRET)
    assert not verify_signature(tampered, SECRET, signature)


def test_the_wrong_secret_fails_verification() -> None:
    payload = b'{"event":"document.failed"}'
    assert not verify_signature(payload, "some-other-secret", sign_payload(payload, SECRET))


def test_signatures_are_deterministic_and_hex() -> None:
    payload = b'{"event":"document.processing"}'
    first = sign_payload(payload, SECRET)
    assert first == sign_payload(payload, SECRET)
    assert len(first) == 64
    assert all(c in "0123456789abcdef" for c in first)


def test_a_malformed_signature_is_rejected_without_raising() -> None:
    payload = b"{}"
    for candidate in ("", "not-hex", "0" * 64):
        assert not verify_signature(payload, SECRET, candidate)
