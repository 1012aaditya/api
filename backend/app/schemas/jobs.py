from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field


class JobAccepted(BaseModel):
    """The response to an asynchronous submission (§6)."""

    success: Literal[True] = True
    request_id: str
    job_id: str
    document_id: str
    status: Literal["queued", "processing", "completed", "failed"] = "queued"


class JobStatusResponse(BaseModel):
    id: str
    status: Literal["queued", "processing", "completed", "failed"]
    document_id: str
    document_type: str
    request_id: str | None
    extraction_id: str | None = Field(
        default=None,
        description="Set once the job completes. Fetch the result from "
        "GET /v1/documents/{document_id}/extraction.",
    )
    attempts: int
    max_attempts: int
    error: dict[str, str] | None = None
    created_at: dt.datetime
    started_at: dt.datetime | None
    completed_at: dt.datetime | None
