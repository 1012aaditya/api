from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models import DeliveryStatus, Webhook, WebhookDelivery


class WebhookRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, organization_id: str, webhook_id: str) -> Webhook | None:
        result = await self.session.execute(
            select(Webhook).where(
                Webhook.id == webhook_id, Webhook.organization_id == organization_id
            )
        )
        return result.scalar_one_or_none()

    async def list_for_organization(self, organization_id: str) -> list[Webhook]:
        result = await self.session.execute(
            select(Webhook)
            .where(Webhook.organization_id == organization_id)
            .order_by(Webhook.created_at.desc())
        )
        return list(result.scalars().all())

    async def subscribers(self, organization_id: str, event: str) -> list[Webhook]:
        """Active endpoints in this organization subscribed to this event."""
        result = await self.session.execute(
            select(Webhook).where(
                Webhook.organization_id == organization_id,
                Webhook.is_active.is_(True),
            )
        )
        return [hook for hook in result.scalars().all() if hook.subscribes_to(event)]

    async def create(
        self,
        *,
        organization_id: str,
        url: str,
        events: list[str],
        description: str | None = None,
    ) -> Webhook:
        webhook = Webhook(
            organization_id=organization_id,
            url=url,
            events=events,
            description=description,
        )
        self.session.add(webhook)
        await self.session.flush()
        return webhook

    async def rotate_secret(self, webhook: Webhook) -> Webhook:
        webhook.secret_version += 1
        await self.session.flush()
        return webhook

    async def delete(self, webhook: Webhook) -> None:
        await self.session.delete(webhook)
        await self.session.flush()

    async def record_success(self, webhook: Webhook) -> None:
        webhook.consecutive_failures = 0
        webhook.last_delivery_at = utcnow()
        await self.session.flush()

    async def record_failure(self, webhook: Webhook, *, threshold: int) -> None:
        webhook.consecutive_failures += 1
        webhook.last_delivery_at = utcnow()
        if webhook.consecutive_failures >= threshold and webhook.is_active:
            # Stop calling an endpoint that has been dead this long. The
            # customer re-enables it once they have fixed it.
            webhook.is_active = False
            webhook.disabled_at = utcnow()
        await self.session.flush()

    async def set_active(self, webhook: Webhook, active: bool) -> Webhook:
        webhook.is_active = active
        webhook.disabled_at = None if active else utcnow()
        if active:
            webhook.consecutive_failures = 0
        await self.session.flush()
        return webhook


class WebhookDeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        organization_id: str,
        webhook_id: str,
        event: str,
        payload: dict[str, Any],
        job_id: str | None = None,
        document_id: str | None = None,
        max_attempts: int = 5,
    ) -> WebhookDelivery:
        delivery = WebhookDelivery(
            organization_id=organization_id,
            webhook_id=webhook_id,
            event=event,
            payload=payload,
            job_id=job_id,
            document_id=document_id,
            max_attempts=max_attempts,
            next_attempt_at=utcnow(),
        )
        self.session.add(delivery)
        await self.session.flush()
        return delivery

    async def due(
        self, *, now: dt.datetime | None = None, limit: int = 20
    ) -> list[WebhookDelivery]:
        result = await self.session.execute(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.status == DeliveryStatus.PENDING,
                WebhookDelivery.next_attempt_at <= (now or utcnow()),
            )
            .order_by(WebhookDelivery.next_attempt_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_for_organization(
        self, organization_id: str, *, limit: int = 50, webhook_id: str | None = None
    ) -> list[WebhookDelivery]:
        query = select(WebhookDelivery).where(
            WebhookDelivery.organization_id == organization_id
        )
        if webhook_id is not None:
            query = query.where(WebhookDelivery.webhook_id == webhook_id)
        result = await self.session.execute(
            query.order_by(WebhookDelivery.created_at.desc()).limit(min(limit, 200))
        )
        return list(result.scalars().all())

    async def mark_delivered(
        self, delivery: WebhookDelivery, *, response_status: int
    ) -> WebhookDelivery:
        delivery.status = DeliveryStatus.DELIVERED
        delivery.response_status = response_status
        delivery.delivered_at = utcnow()
        delivery.error = None
        await self.session.flush()
        return delivery

    async def mark_attempt_failed(
        self,
        delivery: WebhookDelivery,
        *,
        error: str,
        response_status: int | None,
        retry_in_seconds: int | None,
    ) -> WebhookDelivery:
        delivery.response_status = response_status
        delivery.error = error[:300]
        if retry_in_seconds is not None and delivery.attempts < delivery.max_attempts:
            delivery.next_attempt_at = utcnow() + dt.timedelta(seconds=retry_in_seconds)
        else:
            delivery.status = DeliveryStatus.FAILED
        await self.session.flush()
        return delivery
