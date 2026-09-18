from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class RejectedFile(BaseModel):
    """A file the batch refused, and why. Never silently dropped."""

    filename: str
    code: str
    message: str


class BatchAccepted(BaseModel):
    success: bool = True
    request_id: str
    batch_id: str
    accepted: int
    rejected: list[RejectedFile] = Field(default_factory=list)
    job_ids: list[str] = Field(default_factory=list)


class BatchProgress(BaseModel):
    id: str
    name: str | None
    document_count: int
    rejected_count: int
    total: int
    queued: int
    processing: int
    completed: int
    failed: int
    done: bool = Field(
        default=False, description="True when no job in the batch is still pending."
    )
    created_at: dt.datetime
