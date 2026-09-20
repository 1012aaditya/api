"""Webhook endpoint management (§22).

Session-authenticated, like API keys: a webhook secret is a credential, and a
leaked API key must not be able to redirect a customer's document events to an
attacker's server.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import UserContext, get_current_user, get_request_id, require_privileged_user
from app.core.config import Settings, get_settings
from app.core.errors import ConflictError, NotFoundError, ProviderUnavailableError
from app.core.logging import get_logger
from app.db.session import get_db
from app.models import Webhook, WebhookDelivery
from app.repositories.webhooks import WebhookDeliveryRepository, WebhookRepository
from app.schemas.common import SuccessResponse
from app.schemas.webhooks import (
    CreatedWebhook,
    CreateWebhookRequest,
    DeliverySummary,
    WebhookSummary,
)
from app.services.webhook_security import assert_destination_is_safe, derive_secret

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = get_logger("docuparse.webhooks.api")


class WebhooksNotConfiguredError(ProviderUnavailableError):
    code = "webhooks_not_configured"
    message = (
        "Webhooks are not configured on this deployment. Set WEBHOOK_SECRET to "
        "enable them — without it there is nothing to sign deliveries with, and "
        "an unsigned webhook is worse than none."
    )


def _summary(webhook: Webhook) -> WebhookSummary:
    return WebhookSummary(
        id=webhook.id,
        url=webhook.url,
        description=webhook.description,
        events=list(webhook.events or []),
        is_active=webhook.is_active,
        consecutive_failures=webhook.consecutive_failures,
        last_delivery_at=webhook.last_delivery_at,
        disabled_at=webhook.disabled_at,
        created_at=webhook.created_at,
    )


def _delivery(delivery: WebhookDelivery) -> DeliverySummary:
    return DeliverySummary(
        id=delivery.id,
        webhook_id=delivery.webhook_id,
        event=delivery.event,
        status=delivery.status,
        job_id=delivery.job_id,
        document_id=delivery.document_id,
        attempts=delivery.attempts,
        max_attempts=delivery.max_attempts,
        response_status=delivery.response_status,
        error=delivery.error,
        next_attempt_at=delivery.next_attempt_at,
        delivered_at=delivery.delivered_at,
        created_at=delivery.created_at,
        payload=delivery.payload or {},
    )


def _secret_for(webhook: Webhook, settings: Settings) -> str:
    return derive_secret(
        webhook_id=webhook.id,
        version=webhook.secret_version,
        master_secret=settings.webhook_secret or "",
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessResponse[CreatedWebhook],
    summary="Register an endpoint (the signing secret is shown once)",
)
async def create_webhook(
    payload: CreateWebhookRequest,
    context: UserContext = Depends(require_privileged_user),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[CreatedWebhook]:
    if not settings.webhooks_configured:
        raise WebhooksNotConfiguredError()

    # Rejects private, loopback, link-local and reserved destinations. A
    # customer-supplied URL is an SSRF vector, not just a string.
    destination = await assert_destination_is_safe(payload.url, settings)

    webhook = await WebhookRepository(db).create(
        organization_id=context.organization.id,
        url=destination.url,
        events=list(dict.fromkeys(payload.events)),
        description=payload.description,
    )
    logger.info(
        "webhook.created",
        organization_id=context.organization.id,
        webhook_id=webhook.id,
        events=webhook.events,
    )
    return SuccessResponse(
        request_id=request_id,
        data=CreatedWebhook(
            **_summary(webhook).model_dump(), secret=_secret_for(webhook, settings)
        ),
    )


@router.get(
    "",
    response_model=SuccessResponse[list[WebhookSummary]],
    summary="List registered endpoints",
)
async def list_webhooks(
    context: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[WebhookSummary]]:
    hooks = await WebhookRepository(db).list_for_organization(context.organization.id)
    return SuccessResponse(request_id=request_id, data=[_summary(h) for h in hooks])


@router.delete(
    "/{webhook_id}",
    response_model=SuccessResponse[dict],
    summary="Delete an endpoint",
)
async def delete_webhook(
    webhook_id: str,
    context: UserContext = Depends(require_privileged_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[dict]:
    repo = WebhookRepository(db)
    webhook = await repo.get(context.organization.id, webhook_id)
    if webhook is None:
        raise NotFoundError("No webhook with that id exists in this organization.")
    await repo.delete(webhook)
    logger.info(
        "webhook.deleted",
        organization_id=context.organization.id,
        webhook_id=webhook_id,
    )
    return SuccessResponse(request_id=request_id, data={"id": webhook_id, "deleted": True})


@router.post(
    "/{webhook_id}/rotate",
    response_model=SuccessResponse[CreatedWebhook],
    summary="Issue a new signing secret for this endpoint",
)
async def rotate_webhook_secret(
    webhook_id: str,
    context: UserContext = Depends(require_privileged_user),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[CreatedWebhook]:
    if not settings.webhooks_configured:
        raise WebhooksNotConfiguredError()
    repo = WebhookRepository(db)
    webhook = await repo.get(context.organization.id, webhook_id)
    if webhook is None:
        raise NotFoundError("No webhook with that id exists in this organization.")

    # Immediate, with no overlap: deliveries from this moment carry the new
    # signature, so update the receiver before rotating.
    await repo.rotate_secret(webhook)
    return SuccessResponse(
        request_id=request_id,
        data=CreatedWebhook(
            **_summary(webhook).model_dump(), secret=_secret_for(webhook, settings)
        ),
    )


@router.post(
    "/{webhook_id}/enable",
    response_model=SuccessResponse[WebhookSummary],
    summary="Re-enable an endpoint we disabled after repeated failures",
)
async def enable_webhook(
    webhook_id: str,
    context: UserContext = Depends(require_privileged_user),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[WebhookSummary]:
    repo = WebhookRepository(db)
    webhook = await repo.get(context.organization.id, webhook_id)
    if webhook is None:
        raise NotFoundError("No webhook with that id exists in this organization.")
    if webhook.is_active:
        raise ConflictError("That endpoint is already active.")
    # Re-check the destination: it may have been repointed while disabled.
    await assert_destination_is_safe(webhook.url, settings)
    await repo.set_active(webhook, True)
    return SuccessResponse(request_id=request_id, data=_summary(webhook))


@router.post(
    "/{webhook_id}/disable",
    response_model=SuccessResponse[WebhookSummary],
    summary="Stop delivering to an endpoint without deleting it",
)
async def disable_webhook(
    webhook_id: str,
    context: UserContext = Depends(require_privileged_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[WebhookSummary]:
    repo = WebhookRepository(db)
    webhook = await repo.get(context.organization.id, webhook_id)
    if webhook is None:
        raise NotFoundError("No webhook with that id exists in this organization.")
    await repo.set_active(webhook, False)
    return SuccessResponse(request_id=request_id, data=_summary(webhook))


@router.get(
    "/deliveries",
    response_model=SuccessResponse[list[DeliverySummary]],
    summary="Recent delivery attempts, newest first",
)
async def list_deliveries(
    webhook_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    context: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[DeliverySummary]]:
    deliveries = await WebhookDeliveryRepository(db).list_for_organization(
        context.organization.id, limit=limit, webhook_id=webhook_id
    )
    return SuccessResponse(request_id=request_id, data=[_delivery(d) for d in deliveries])
