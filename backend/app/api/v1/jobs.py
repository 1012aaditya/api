"""Job status (§6).

Poll ``GET /v1/jobs/{job_id}`` until ``status`` is ``completed`` or
``failed`` — or register a webhook and skip the polling.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, enforce_rate_limit, get_request_id
from app.core.errors import NotFoundError
from app.db.session import get_db
from app.models import ExtractionJob
from app.repositories.jobs import JobRepository
from app.schemas.common import SuccessResponse
from app.schemas.jobs import JobStatusResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _status(job: ExtractionJob) -> JobStatusResponse:
    return JobStatusResponse(
        id=job.id,
        status=job.status,  # type: ignore[arg-type]
        document_id=job.document_id,
        document_type=job.document_type,
        request_id=job.request_id,
        extraction_id=job.extraction_id,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        error=(
            {"code": job.error_code, "message": job.error_message or ""}
            if job.error_code
            else None
        ),
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.get(
    "",
    response_model=SuccessResponse[list[JobStatusResponse]],
    summary="List jobs, newest first",
)
async def list_jobs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[JobStatusResponse]]:
    jobs = await JobRepository(db).list_for_organization(
        auth.organization_id, limit=limit, offset=offset
    )
    return SuccessResponse(request_id=request_id, data=[_status(job) for job in jobs])


@router.get(
    "/{job_id}",
    response_model=SuccessResponse[JobStatusResponse],
    summary="Check a job's status",
)
async def get_job(
    job_id: str,
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[JobStatusResponse]:
    job = await JobRepository(db).get(auth.organization_id, job_id)
    if job is None:
        # Another organization's job is reported as absent, not forbidden.
        raise NotFoundError("No job with that id exists in this organization.")
    return SuccessResponse(request_id=request_id, data=_status(job))
