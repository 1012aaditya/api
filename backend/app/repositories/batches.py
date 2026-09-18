from __future__ import annotations

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DocumentBatch, ExtractionJob, JobStatus


class BatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, organization_id: str, batch_id: str) -> DocumentBatch | None:
        result = await self.session.execute(
            select(DocumentBatch).where(
                DocumentBatch.id == batch_id,
                DocumentBatch.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_organization(
        self, organization_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[DocumentBatch]:
        result = await self.session.execute(
            select(DocumentBatch)
            .where(DocumentBatch.organization_id == organization_id)
            .order_by(DocumentBatch.created_at.desc())
            .limit(min(limit, 200))
            .offset(offset)
        )
        return list(result.scalars().all())

    async def create(
        self, *, organization_id: str, name: str | None, request_id: str | None
    ) -> DocumentBatch:
        batch = DocumentBatch(
            organization_id=organization_id, name=name, request_id=request_id
        )
        self.session.add(batch)
        await self.session.flush()
        return batch

    async def progress(self, organization_id: str, batch_id: str) -> dict[str, int]:
        """Counts per job state.

        Derived from the jobs rather than kept as a second copy: a counter
        that has to be updated in step with the jobs is a counter that will
        eventually disagree with them.
        """
        result = await self.session.execute(
            select(
                func.count().label("total"),
                func.sum(
                    func.cast(ExtractionJob.status == JobStatus.QUEUED, Integer)
                ).label("queued"),
                func.sum(
                    func.cast(ExtractionJob.status == JobStatus.PROCESSING, Integer)
                ).label("processing"),
                func.sum(
                    func.cast(ExtractionJob.status == JobStatus.COMPLETED, Integer)
                ).label("completed"),
                func.sum(
                    func.cast(ExtractionJob.status == JobStatus.FAILED, Integer)
                ).label("failed"),
            ).where(
                ExtractionJob.organization_id == organization_id,
                ExtractionJob.batch_id == batch_id,
            )
        )
        row = result.one()
        return {
            "total": int(row.total or 0),
            "queued": int(row.queued or 0),
            "processing": int(row.processing or 0),
            "completed": int(row.completed or 0),
            "failed": int(row.failed or 0),
        }
