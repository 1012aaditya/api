"""The agent's own queue: follow-ups, calls, case re-checks.

Same claim mechanics as ``extraction_jobs`` — ``FOR UPDATE SKIP LOCKED`` on
PostgreSQL, a conditional update everywhere else — for the same reason: two
workers must never take the same row, and a reminder sent twice is worse than
one sent late.

The whole follow-up schedule is rows with future ``available_at`` timestamps.
There is no sleeping process holding a timer, so a worker restart loses
nothing and a reminder due at 3am is simply a row that becomes claimable at
3am.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models import AgentJob, AgentJobStatus


class AgentJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def _is_postgres(self) -> bool:
        return (
            self.session.bind is not None
            and self.session.bind.dialect.name == "postgresql"
        )

    async def get(self, organization_id: str, job_id: str) -> AgentJob | None:
        result = await self.session.execute(
            select(AgentJob).where(
                AgentJob.id == job_id, AgentJob.organization_id == organization_id
            )
        )
        return result.scalar_one_or_none()

    async def schedule(
        self,
        *,
        organization_id: str,
        type: str,
        dedupe_key: str,
        available_at: dt.datetime,
        client_id: str | None = None,
        case_id: str | None = None,
        payload: dict | None = None,
        attempt_number: int = 1,
        max_attempts: int = 3,
    ) -> AgentJob:
        """Queue work, or leave the already-queued one alone.

        ``dedupe_key`` is the identity of the *intention* — "remind this case
        about its missing documents" — not of the run that formed it. Running
        the scheduler twice must not stack two reminders, and a scheduler that
        runs every few minutes will form the same intention constantly.

        An existing job that has already run does not block a new one: that is
        the next follow-up in the sequence, and it carries a different key.
        """
        existing = (
            await self.session.execute(
                select(AgentJob).where(AgentJob.dedupe_key == dedupe_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        job = AgentJob(
            organization_id=organization_id,
            client_id=client_id,
            case_id=case_id,
            type=type,
            payload=payload or {},
            dedupe_key=dedupe_key,
            available_at=available_at,
            attempt_number=attempt_number,
            max_attempts=max_attempts,
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def claim_next(self, *, now: dt.datetime | None = None) -> AgentJob | None:
        """Take one runnable job, atomically."""
        moment = now or utcnow()
        query = (
            select(AgentJob)
            .where(
                AgentJob.status == AgentJobStatus.QUEUED,
                AgentJob.available_at <= moment,
            )
            .order_by(AgentJob.available_at, AgentJob.created_at)
            .limit(1)
        )
        if self._is_postgres:
            query = query.with_for_update(skip_locked=True)

        job = (await self.session.execute(query)).scalar_one_or_none()
        if job is None:
            return None

        # The status in the WHERE is what makes this safe without SKIP LOCKED:
        # a loser's update matches zero rows.
        claimed = await self.session.execute(
            update(AgentJob)
            .where(
                AgentJob.id == job.id,
                AgentJob.status == AgentJobStatus.QUEUED,
            )
            .values(
                status=AgentJobStatus.PROCESSING,
                attempts=AgentJob.attempts + 1,
                claimed_at=moment,
                updated_at=moment,
            )
        )
        if claimed.rowcount == 0:
            return None
        await self.session.refresh(job)
        return job

    async def mark_done(self, job: AgentJob, *, now: dt.datetime | None = None) -> AgentJob:
        job.status = AgentJobStatus.COMPLETED
        job.completed_at = now or utcnow()
        job.last_error = None
        await self.session.flush()
        return job

    async def mark_failed(
        self,
        job: AgentJob,
        *,
        error: str,
        retry_in_seconds: int | None = None,
        now: dt.datetime | None = None,
    ) -> AgentJob:
        moment = now or utcnow()
        job.last_error = error[:1000]
        if retry_in_seconds is not None and job.attempts < job.max_attempts:
            job.status = AgentJobStatus.QUEUED
            job.available_at = moment + dt.timedelta(seconds=retry_in_seconds)
        else:
            job.status = AgentJobStatus.FAILED
            job.completed_at = moment
        await self.session.flush()
        return job

    async def cancel_for_case(
        self, organization_id: str, case_id: str, *, reason: str = "no longer needed"
    ) -> int:
        """Drop queued work for a case.

        Called when a case stops needing chasing — everything arrived, or a
        human took it over. A reminder that goes out after the client has
        already sent everything is the fastest way to lose their patience.
        """
        result = await self.session.execute(
            update(AgentJob)
            .where(
                AgentJob.organization_id == organization_id,
                AgentJob.case_id == case_id,
                AgentJob.status == AgentJobStatus.QUEUED,
            )
            .values(
                status=AgentJobStatus.CANCELLED,
                last_error=reason,
                completed_at=utcnow(),
            )
        )
        await self.session.flush()
        return int(result.rowcount or 0)

    async def cancel_for_client(self, organization_id: str, client_id: str, *, reason: str) -> int:
        result = await self.session.execute(
            update(AgentJob)
            .where(
                AgentJob.organization_id == organization_id,
                AgentJob.client_id == client_id,
                AgentJob.status == AgentJobStatus.QUEUED,
            )
            .values(
                status=AgentJobStatus.CANCELLED,
                last_error=reason,
                completed_at=utcnow(),
            )
        )
        await self.session.flush()
        return int(result.rowcount or 0)

    async def release_stale(self, *, older_than: dt.datetime) -> int:
        """Requeue jobs a dead worker left claimed."""
        result = await self.session.execute(
            update(AgentJob)
            .where(
                AgentJob.status == AgentJobStatus.PROCESSING,
                AgentJob.claimed_at < older_than,
            )
            .values(status=AgentJobStatus.QUEUED, updated_at=utcnow())
        )
        await self.session.flush()
        return int(result.rowcount or 0)

    async def pending_for_case(self, organization_id: str, case_id: str) -> list[AgentJob]:
        result = await self.session.execute(
            select(AgentJob)
            .where(
                AgentJob.organization_id == organization_id,
                AgentJob.case_id == case_id,
                AgentJob.status == AgentJobStatus.QUEUED,
            )
            .order_by(AgentJob.available_at)
        )
        return list(result.scalars().all())

    async def attempts_for_case(self, organization_id: str, case_id: str) -> int:
        """How many follow-ups have already gone out for this case.

        Counted from jobs that ran, so the per-case cap cannot drift from what
        actually happened.
        """
        result = await self.session.execute(
            select(func.count())
            .select_from(AgentJob)
            .where(
                AgentJob.organization_id == organization_id,
                AgentJob.case_id == case_id,
                AgentJob.status == AgentJobStatus.COMPLETED,
            )
        )
        return int(result.scalar_one())
