"""Webhook fan-out and delivery (§22).

Emitting an event and delivering it are deliberately separate. Emitting
writes a ``webhook_deliveries`` row inside the same transaction as the work
that caused it, so an event can never be lost because the HTTP call failed.
Delivery is a separate, retried pass over those rows.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.base import utcnow
from app.models import Webhook, WebhookDelivery, WebhookEvent
from app.repositories.webhooks import WebhookDeliveryRepository, WebhookRepository
from app.services.webhook_security import (
    SIGNATURE_HEADER,
    UnsafeWebhookURLError,
    assert_destination_is_safe,
    derive_secret,
    sign_request,
)

logger = get_logger("docuparse.webhooks")

USER_AGENT = "DocuParse-Webhooks/1.0"
BACKOFF_BASE_SECONDS = 30
BACKOFF_CAP_SECONDS = 6 * 60 * 60
# 4xx means the receiver understood and refused. Retrying will not help, so
# these fail the delivery immediately rather than burning the attempt budget.
_PERMANENT_STATUSES = frozenset({400, 401, 403, 404, 405, 410, 422})
# Except these two, which explicitly mean "try again".
_RETRYABLE_4XX = frozenset({408, 429})


def backoff_seconds(attempt: int) -> int:
    """Exponential, capped. 30s, 1m, 2m, 4m, 8m … up to six hours."""
    return min(BACKOFF_BASE_SECONDS * 2 ** max(0, attempt - 1), BACKOFF_CAP_SECONDS)


def build_payload(
    *,
    event: str,
    job_id: str | None,
    document_id: str | None,
    status: str,
    delivery_id: str | None = None,
    extraction_id: str | None = None,
    validation_overall: str | None = None,
    error_code: str | None = None,
    occurred_at: dt.datetime | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event": event,
        "job_id": job_id,
        "document_id": document_id,
        "status": status,
        "occurred_at": (occurred_at or utcnow()).isoformat(),
    }
    if delivery_id:
        # An idempotency key for the receiver: a retried delivery carries the
        # same id, so it can recognise a duplicate rather than double-post.
        payload["id"] = delivery_id
    if extraction_id:
        payload["extraction_id"] = extraction_id
    if validation_overall:
        payload["validation"] = {"overall": validation_overall}
    if error_code:
        payload["error"] = {"code": error_code}
    return payload


async def emit_event(
    session: AsyncSession,
    *,
    organization_id: str,
    event: str,
    job_id: str | None = None,
    document_id: str | None = None,
    status: str,
    extraction_id: str | None = None,
    validation_overall: str | None = None,
    error_code: str | None = None,
    settings: Settings | None = None,
) -> list[WebhookDelivery]:
    """Queue this event for every endpoint subscribed to it.

    Writes rows only — nothing is sent here. The caller's transaction decides
    whether the event happened at all.
    """
    settings = settings or get_settings()
    if event not in WebhookEvent.ALL:
        raise ValueError(f"unknown webhook event: {event!r}")

    subscribers = await WebhookRepository(session).subscribers(organization_id, event)
    if not subscribers:
        return []

    deliveries = WebhookDeliveryRepository(session)
    created: list[WebhookDelivery] = []
    for webhook in subscribers:
        delivery = await deliveries.create(
            organization_id=organization_id,
            webhook_id=webhook.id,
            event=event,
            payload={},
            job_id=job_id,
            document_id=document_id,
            max_attempts=settings.webhook_max_attempts,
        )
        delivery.payload = build_payload(
            event=event,
            job_id=job_id,
            document_id=document_id,
            status=status,
            delivery_id=delivery.id,
            extraction_id=extraction_id,
            validation_overall=validation_overall,
            error_code=error_code,
        )
        created.append(delivery)

    await session.flush()
    logger.info(
        "webhook.event_queued",
        organization_id=organization_id,
        webhook_event=event,
        job_id=job_id,
        subscribers=len(created),
    )
    return created


@dataclass
class DeliveryOutcome:
    delivered: bool
    response_status: int | None = None
    error: str | None = None
    retryable: bool = False


async def attempt_delivery(
    delivery: WebhookDelivery,
    webhook: Webhook,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
) -> DeliveryOutcome:
    """Send one delivery. Never raises — the outcome is the return value."""
    try:
        # Re-checked on every attempt, not just at registration: DNS can
        # change under us between the two.
        await assert_destination_is_safe(webhook.url, settings)
    except UnsafeWebhookURLError as exc:
        return DeliveryOutcome(False, None, f"unsafe destination: {exc.message}", False)

    secret = derive_secret(
        webhook_id=webhook.id,
        version=webhook.secret_version,
        master_secret=settings.webhook_secret or "",
    )
    body = json.dumps(delivery.payload, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        SIGNATURE_HEADER: sign_request(body, secret),
        "X-DocuParse-Event": delivery.event,
        "X-DocuParse-Delivery": delivery.id,
        "X-DocuParse-Attempt": str(delivery.attempts),
    }

    try:
        response = await client.post(
            webhook.url,
            content=body,
            headers=headers,
            timeout=settings.webhook_timeout_seconds,
            follow_redirects=False,  # A redirect is another SSRF hop.
        )
    except httpx.TimeoutException:
        return DeliveryOutcome(False, None, "timed out", True)
    except httpx.TransportError as exc:
        return DeliveryOutcome(False, None, f"connection failed: {type(exc).__name__}", True)

    if 200 <= response.status_code < 300:
        return DeliveryOutcome(True, response.status_code)

    permanent = (
        response.status_code in _PERMANENT_STATUSES
        and response.status_code not in _RETRYABLE_4XX
    )
    return DeliveryOutcome(
        False,
        response.status_code,
        # The receiver's response body is somebody else's server talking; it
        # is not recorded, only the status.
        f"endpoint returned {response.status_code}",
        retryable=not permanent,
    )


async def deliver_due(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    limit: int = 20,
    now: dt.datetime | None = None,
) -> dict[str, int]:
    """Send every delivery that is due. Returns a small tally for logging."""
    settings = settings or get_settings()
    deliveries = WebhookDeliveryRepository(session)
    webhooks = WebhookRepository(session)
    due = await deliveries.due(now=now, limit=limit)
    if not due:
        return {"attempted": 0, "delivered": 0, "retrying": 0, "failed": 0}

    owned_client = client is None
    http = client or httpx.AsyncClient()
    tally = {"attempted": 0, "delivered": 0, "retrying": 0, "failed": 0}
    try:
        for delivery in due:
            webhook = await webhooks.get(delivery.organization_id, delivery.webhook_id)
            if webhook is None:
                await deliveries.mark_attempt_failed(
                    delivery, error="endpoint no longer exists",
                    response_status=None, retry_in_seconds=None,
                )
                tally["failed"] += 1
                continue

            delivery.attempts += 1
            tally["attempted"] += 1
            outcome = await attempt_delivery(
                delivery, webhook, settings=settings, client=http
            )

            if outcome.delivered:
                await deliveries.mark_delivered(
                    delivery, response_status=outcome.response_status or 200
                )
                await webhooks.record_success(webhook)
                tally["delivered"] += 1
                logger.info(
                    "webhook.delivered",
                    organization_id=delivery.organization_id,
                    delivery_id=delivery.id,
                    webhook_event=delivery.event,
                    attempt=delivery.attempts,
                    status=outcome.response_status,
                )
                continue

            will_retry = outcome.retryable and delivery.attempts < delivery.max_attempts
            await deliveries.mark_attempt_failed(
                delivery,
                error=outcome.error or "delivery failed",
                response_status=outcome.response_status,
                retry_in_seconds=backoff_seconds(delivery.attempts) if will_retry else None,
            )
            await webhooks.record_failure(
                webhook, threshold=settings.webhook_failure_threshold
            )
            tally["retrying" if will_retry else "failed"] += 1
            logger.warning(
                "webhook.delivery_failed",
                organization_id=delivery.organization_id,
                delivery_id=delivery.id,
                webhook_event=delivery.event,
                attempt=delivery.attempts,
                status=outcome.response_status,
                will_retry=will_retry,
            )
        await session.commit()
    finally:
        if owned_client:
            await http.aclose()
    return tally
