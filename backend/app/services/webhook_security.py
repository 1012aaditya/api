"""Webhook signing and destination validation (§22, §23).

Two problems live here, and both are security problems.

**Signing.** A receiver must be able to tell our call from anyone else's, so
every delivery is signed. The per-endpoint secret is *derived* from the
deployment's master ``WEBHOOK_SECRET`` rather than stored, which means there
is no webhook secret at rest in the database to leak, and rotating one
endpoint is a version bump.

**Destination.** The URL is supplied by the customer, so it is an SSRF vector:
left unchecked, "deliver my webhook to ``http://169.254.169.254/``" turns this
service into a proxy for reading cloud instance metadata. Every destination is
resolved and checked against private, loopback, link-local and reserved
address space before we will connect to it.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import anyio

from app.core.config import Settings
from app.core.errors import InvalidRequestError
from app.core.logging import get_logger

logger = get_logger("docuparse.webhooks.security")

SIGNATURE_HEADER = "X-DocuParse-Signature"
TIMESTAMP_TOLERANCE_SECONDS = 300
_SECRET_PREFIX = "whsec_"
_ALLOWED_SCHEMES = frozenset({"http", "https"})
# Ports that are almost never a real webhook receiver and often are something
# we should not be poking from inside a customer's network.
_BLOCKED_PORTS = frozenset({22, 23, 25, 445, 3306, 5432, 6379, 9200, 11211, 27017})


# --- Signing -----------------------------------------------------------


def derive_secret(*, webhook_id: str, version: int, master_secret: str) -> str:
    """The endpoint's signing secret. Deterministic, never stored."""
    digest = hmac.new(
        master_secret.encode("utf-8"),
        f"webhook:{webhook_id}:v{version}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{_SECRET_PREFIX}{digest}"


def sign_request(payload: bytes, secret: str, *, timestamp: int | None = None) -> str:
    """Sign the body, timestamped.

    The timestamp is inside the signed material, so a captured delivery
    cannot be replayed later — the receiver rejects a stale ``t``.
    """
    issued_at = timestamp if timestamp is not None else int(time.time())
    signed = f"{issued_at}.".encode() + payload
    mac = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={issued_at},v1={mac}"


def verify_request(
    payload: bytes,
    secret: str,
    header: str,
    *,
    tolerance_seconds: int = TIMESTAMP_TOLERANCE_SECONDS,
    now: int | None = None,
) -> bool:
    """What a receiver runs. Published in the docs so integrators copy this."""
    parts = dict(
        piece.split("=", 1) for piece in header.split(",") if "=" in piece
    )
    timestamp, received = parts.get("t"), parts.get("v1")
    if not timestamp or not received:
        return False
    try:
        issued_at = int(timestamp)
    except ValueError:
        return False

    current = now if now is not None else int(time.time())
    if abs(current - issued_at) > tolerance_seconds:
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        f"{issued_at}.".encode() + payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, received)


# --- Destination validation --------------------------------------------


@dataclass(frozen=True)
class WebhookDestination:
    url: str
    host: str
    port: int
    scheme: str


class UnsafeWebhookURLError(InvalidRequestError):
    code = "invalid_webhook_url"


def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _is_never_allowed(address: str) -> bool:
    """Addresses no webhook may target, development flag or not.

    ``WEBHOOK_ALLOW_PRIVATE_URLS`` exists so a developer can point a webhook
    at a receiver on their own machine. It is not a reason to let one reach
    the cloud metadata service — that is never a webhook endpoint, and being
    able to read it is the whole prize in a webhook SSRF.
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_link_local or ip.is_multicast or ip.is_reserved


def parse_destination(url: str, settings: Settings) -> WebhookDestination:
    """Structural checks. Cheap, and they run before any DNS lookup."""
    if len(url) > 2000:
        raise UnsafeWebhookURLError("The webhook URL is too long.")

    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsafeWebhookURLError("The webhook URL must start with https:// or http://.")
    insecure = scheme == 'http' and settings.webhook_require_https
    if insecure and not settings.webhook_allow_private_urls:
        raise UnsafeWebhookURLError(
            "Webhook URLs must use https. Document contents are signed but not "
            "encrypted by us in transit otherwise."
        )

    host = (parsed.hostname or "").strip()
    if not host:
        raise UnsafeWebhookURLError("The webhook URL has no host.")
    if parsed.username or parsed.password:
        raise UnsafeWebhookURLError("Credentials in the webhook URL are not supported.")

    port = parsed.port or (443 if scheme == "https" else 80)
    if port in _BLOCKED_PORTS:
        raise UnsafeWebhookURLError(f"Port {port} is not a permitted webhook destination.")

    return WebhookDestination(url=url.strip(), host=host, port=port, scheme=scheme)


def _resolve(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeWebhookURLError(
            f"The webhook host {host!r} could not be resolved."
        ) from exc
    return [info[4][0] for info in infos]


async def assert_destination_is_safe(url: str, settings: Settings) -> WebhookDestination:
    """Full check: structure, then every address the host resolves to.

    Known residual risk: DNS can change between this check and the request
    (rebinding). Re-running it immediately before each delivery — which the
    sender does — narrows that window to the length of one connection rather
    than closing it. Closing it entirely needs connection-level IP pinning.
    """
    destination = parse_destination(url, settings)
    addresses = await anyio.to_thread.run_sync(
        _resolve, destination.host, destination.port
    )

    if settings.webhook_allow_private_urls:
        # Development only, so a local receiver can be used. Never set in
        # production: it re-opens most of the path this function exists to
        # close. The link-local and reserved ranges stay closed regardless.
        if settings.is_production:
            logger.error("webhook.private_urls_allowed_in_production")
        forbidden = [a for a in addresses if _is_never_allowed(a)]
        if forbidden:
            logger.warning(
                "webhook.metadata_destination_rejected", host=destination.host
            )
            raise UnsafeWebhookURLError(
                f"{destination.host!r} resolves to a link-local or reserved "
                "address. That is never a webhook endpoint, and it stays "
                "blocked even with WEBHOOK_ALLOW_PRIVATE_URLS set."
            )
        return destination

    unsafe = [address for address in addresses if not _is_public(address)]
    if unsafe or not addresses:
        logger.warning(
            "webhook.unsafe_destination_rejected",
            host=destination.host,
            resolved_count=len(addresses),
        )
        raise UnsafeWebhookURLError(
            f"{destination.host!r} resolves to a private or reserved address. "
            "Webhook endpoints must be reachable on the public internet."
        )
    return destination
