"""Job queue access.

The claim is the only interesting query here: several workers may run at
once, and exactly one must get each job.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models import ExtractionJob, JobStatus


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def _is_postgres(self) -> bool:
        return self.session.bind is not None and self.session.bind.dialect.name == "postgresql"

    async def get(self, organization_id: str, job_id: str) -> ExtractionJob | None:
        result = await self.session.execute(
            select(ExtractionJob).where(
                ExtractionJob.id == job_id,
                ExtractionJob.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_organization(
        self, organization_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[ExtractionJob]:
        result = await self.session.execute(
            select(ExtractionJob)
            .where(ExtractionJob.organization_id == organization_id)
            .order_by(ExtractionJob.created_at.desc())
            .limit(min(limit, 200))
            .offset(offset)
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        organization_id: str,
        document_id: str,
        request_id: str | None,
        api_key_id: str | None = None,
        document_type: str = "gst_invoice",
        max_attempts: int = 3,
    ) -> ExtractionJob:
        job = ExtractionJob(
            organization_id=organization_id,
            document_id=document_id,
            request_id=request_id,
            api_key_id=api_key_id,
            document_type=document_type,
            max_attempts=max_attempts,
            available_at=utcnow(),
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def claim_next(self, *, now: dt.datetime | None = None) -> ExtractionJob | None:
        """Take one runnable job, atomically.

        On PostgreSQL this is ``SELECT ... FOR UPDATE SKIP LOCKED``: two
        workers racing take two different rows rather than both taking the
        same one. SQLite has no SKIP LOCKED and no concurrent writers, so the
        test path falls back to a plain conditional update, which is correct
        for the single worker it will ever have.
        """
        now = now or utcnow()
        query = (
            select(ExtractionJob)
            .where(
                ExtractionJob.status == JobStatus.QUEUED,
                ExtractionJob.available_at <= now,
            )
            .order_by(ExtractionJob.available_at, ExtractionJob.created_at)
            .limit(1)
        )
        if self._is_postgres:
            query = query.with_for_update(skip_locked=True)

        job = (await self.session.execute(query)).scalar_one_or_none()
        if job is None:
            return None

        # The WHERE clause on status is what makes this safe without
        # SKIP LOCKED: a loser's update matches zero rows.
        claimed = await self.session.execute(
            update(ExtractionJob)
            .where(
                ExtractionJob.id == job.id,
                ExtractionJob.status == JobStatus.QUEUED,
            )
            .values(
                status=JobStatus.PROCESSING,
                attempts=ExtractionJob.attempts + 1,
                started_at=now,
                updated_at=now,
            )
        )
        if claimed.rowcount == 0:
            return None
        await self.session.refresh(job)
        return job

    async def release_stale(self, *, older_than: dt.datetime) -> int:
        """Requeue jobs a dead worker left in ``processing``."""
        result = await self.session.execute(
            update(ExtractionJob)
            .where(
                ExtractionJob.status == JobStatus.PROCESSING,
                ExtractionJob.started_at.is_not(None),
                ExtractionJob.started_at < older_than,
            )
            .values(status=JobStatus.QUEUED, available_at=utcnow(), updated_at=utcnow())
        )
        return int(result.rowcount or 0)

    async def mark_completed(self, job: ExtractionJob, *, extraction_id: str) -> ExtractionJob:
        job.status = JobStatus.COMPLETED
        job.extraction_id = extraction_id
        job.completed_at = utcnow()
        job.error_code = None
        job.error_message = None
        await self.session.flush()
        return job

    async def mark_failed(
        self,
        job: ExtractionJob,
        *,
        error_code: str,
        error_message: str,
        retry_in_seconds: int | None = None,
    ) -> ExtractionJob:
        """Fail the job, or schedule a retry if it has attempts left."""
        job.error_code = error_code
        job.error_message = error_message
        if retry_in_seconds is not None and job.attempts < job.max_attempts:
            job.status = JobStatus.QUEUED
            job.available_at = utcnow() + dt.timedelta(seconds=retry_in_seconds)
        else:
            job.status = JobStatus.FAILED
            job.completed_at = utcnow()
        await self.session.flush()
        return job
