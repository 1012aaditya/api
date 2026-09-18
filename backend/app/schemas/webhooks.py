from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field

EventName = Literal["document.processing", "document.completed", "document.failed"]


class CreateWebhookRequest(BaseModel):
    url: str = Field(..., max_length=2000, examples=["https://api.yourapp.com/docuparse"])
    events: list[EventName] = Field(
        default_factory=lambda: ["document.completed", "document.failed"],
        min_length=1,
    )
    description: str | None = Field(default=None, max_length=200)


class WebhookSummary(BaseModel):
    id: str
    url: str
    description: str | None
    events: list[str]
    is_active: bool
    consecutive_failures: int
    last_delivery_at: dt.datetime | None
    disabled_at: dt.datetime | None
    created_at: dt.datetime


class CreatedWebhook(WebhookSummary):
    """Returned once, at creation or rotation."""

    secret: str = Field(
        ...,
        description=(
            "Use this to verify the X-DocuParse-Signature header. Store it now "
            "— it is derived, not stored, and will not be shown again."
        ),
    )


class DeliverySummary(BaseModel):
    id: str
    webhook_id: str
    event: str
    status: str
    job_id: str | None
    document_id: str | None
    attempts: int
    max_attempts: int
    response_status: int | None
    error: str | None
    next_attempt_at: dt.datetime
    delivered_at: dt.datetime | None
    created_at: dt.datetime
    payload: dict[str, Any]
