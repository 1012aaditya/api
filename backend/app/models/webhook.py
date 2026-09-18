from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import ID, Base, JSONType, UTCDateTime, utcnow
from app.utils.ids import prefixed_id


class WebhookEvent:
    """The events a customer can subscribe to (§22)."""

    DOCUMENT_PROCESSING = "document.processing"
    DOCUMENT_COMPLETED = "document.completed"
    DOCUMENT_FAILED = "document.failed"

    ALL = (DOCUMENT_PROCESSING, DOCUMENT_COMPLETED, DOCUMENT_FAILED)


class DeliveryStatus:
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


class Webhook(Base):
    """A customer's subscribed endpoint.

    Note what is *not* here: the signing secret. It is derived on demand from
    the deployment's ``WEBHOOK_SECRET`` plus this row's id and
    ``secret_version``, so there is no webhook secret at rest to leak, and
    rotating one endpoint's secret is a version bump rather than a new row.
    """

    __tablename__ = "webhooks"

    id: Mapped[str] = mapped_column(
        ID, primary_key=True, default=lambda: prefixed_id("whk")
    )
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    events: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)

    secret_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    # Consecutive failures, reset by any success. Used to disable an endpoint
    # that has been dead long enough that we should stop hammering it.
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    disabled_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_delivery_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    def subscribes_to(self, event: str) -> bool:
        return self.is_active and event in (self.events or [])


class WebhookDelivery(Base):
    """One attempt-tracked delivery of one event to one endpoint."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_webhook_deliveries_pending", "status", "next_attempt_at"),
        Index("ix_webhook_deliveries_org_created", "organization_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        ID, primary_key=True, default=lambda: prefixed_id("whd")
    )
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    webhook_id: Mapped[str] = mapped_column(
        ID, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False, index=True
    )

    event: Mapped[str] = mapped_column(String(60), nullable=False)
    job_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    document_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DeliveryStatus.PENDING,
        server_default=text("'pending'"),
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default=text("5")
    )
    next_attempt_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, index=True
    )

    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # A short reason, never the endpoint's response body — that is somebody
    # else's server talking, and we do not want it in our database.
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)

    delivered_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    __mapper_args__ = {"confirm_deleted_rows": False}


