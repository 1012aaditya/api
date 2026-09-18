from __future__ import annotations

import datetime as dt

from sqlalchemy import ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import ID, Base, UTCDateTime, utcnow
from app.utils.ids import job_id


class JobStatus:
    """The four states a job can be in (§6)."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

    TERMINAL = frozenset({COMPLETED, FAILED})


class ExtractionJob(Base):
    """One unit of asynchronous work.

    The queue lives in the database rather than in Redis. At this scale that
    is a feature, not a shortcut: the job and the document row commit in one
    transaction, a stuck job is a row you can look at, and there is no second
    source of truth to drift. Workers claim rows with ``FOR UPDATE SKIP
    LOCKED`` so several can run safely.
    """

    __tablename__ = "extraction_jobs"
    __table_args__ = (
        Index("ix_extraction_jobs_org_created", "organization_id", "created_at"),
        # The claim query's index: find the oldest runnable job, fast.
        Index("ix_extraction_jobs_claim", "status", "available_at"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=job_id)
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(
        ID, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    api_key_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )
    extraction_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    batch_id: Mapped[str | None] = mapped_column(ID, nullable=True, index=True)

    document_type: Mapped[str] = mapped_column(
        String(60), nullable=False, default="gst_invoice", server_default=text("'gst_invoice'")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=JobStatus.QUEUED, server_default=text("'queued'")
    )

    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )
    # When this job becomes eligible to run. Retries push it into the future.
    available_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, index=True
    )

    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in JobStatus.TERMINAL

    @property
    def attempts_remaining(self) -> int:
        return max(0, self.max_attempts - self.attempts)
