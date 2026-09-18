"""Bulk upload (§25).

A folder of invoices, dropped in at once. Each file becomes an ordinary
document and an ordinary job, so the worker, the retry policy and the
webhooks are identical whether a document arrived alone or with a hundred
others. The batch is only a label that makes progress reportable as one
number.

One bad file does not fail the batch: the good ones are queued and the
rejected ones come back with the reason.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, enforce_document_quota, enforce_rate_limit, get_request_id
from app.api.v1.uploads import read_upload
from app.core.errors import DocuParseError, InvalidRequestError, NotFoundError
from app.db.session import get_db
from app.models import DocumentBatch
from app.repositories.batches import BatchRepository
from app.schemas.batches import BatchAccepted, BatchProgress, RejectedFile
from app.schemas.common import ErrorResponse, SuccessResponse
from app.services.extraction_service import ExtractionService, UploadedFile

router = APIRouter(prefix="/batches", tags=["batches"])


def _progress(batch: DocumentBatch, counts: dict[str, int]) -> BatchProgress:
    pending = counts["queued"] + counts["processing"]
    return BatchProgress(
        id=batch.id,
        name=batch.name,
        document_count=batch.document_count,
        rejected_count=batch.rejected_count,
        total=counts["total"],
        queued=counts["queued"],
        processing=counts["processing"],
        completed=counts["completed"],
        failed=counts["failed"],
        done=pending == 0,
        created_at=batch.created_at,
    )


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BatchAccepted,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    summary="Upload many documents at once",
    description=(
        "Send several files as `files` in one multipart request. Every file is "
        "validated on its own: the good ones are queued and the rest are "
        "returned in `rejected` with the reason. Poll "
        "`GET /v1/batches/{batch_id}` for progress, or register a webhook."
    ),
)
async def create_batch(
    files: list[UploadFile] = File(..., description="PDF, PNG, JPG or JPEG files."),
    name: str | None = Form(default=None, max_length=200),
    auth: AuthContext = Depends(enforce_document_quota),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> BatchAccepted:
    if not files:
        raise InvalidRequestError("No files were provided in the 'files' form field.")

    limit = auth.settings.max_batch_files
    if len(files) > limit:
        raise InvalidRequestError(
            f"A batch accepts at most {limit} files; this one has {len(files)}.",
            details={"max_files": limit, "received": len(files)},
        )

    service = ExtractionService(db)
    uploads: list[UploadedFile] = []
    rejected: list[RejectedFile] = []

    for upload in files:
        if not upload.filename:
            continue
        try:
            content = await read_upload(
                upload, max_size_bytes=auth.settings.max_file_size_bytes
            )
        except DocuParseError as exc:
            # Oversized bodies are caught while reading, before the service
            # sees them, so they are collected here rather than there.
            rejected.append(
                RejectedFile(
                    filename=upload.filename, code=exc.code, message=exc.message
                )
            )
            continue
        uploads.append(UploadedFile(content=content, filename=upload.filename))

    batch, jobs, refused = await service.submit_batch(
        auth=auth, uploads=uploads, request_id=request_id, name=name
    )
    rejected.extend(RejectedFile(**item) for item in refused)
    if rejected:
        batch.rejected_count = len(rejected)
        await db.commit()

    return BatchAccepted(
        request_id=request_id,
        batch_id=batch.id,
        accepted=len(jobs),
        rejected=rejected,
        job_ids=[job.id for job in jobs],
    )


@router.get(
    "",
    response_model=SuccessResponse[list[BatchProgress]],
    summary="List batches, newest first",
)
async def list_batches(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[BatchProgress]]:
    repo = BatchRepository(db)
    batches = await repo.list_for_organization(
        auth.organization_id, limit=limit, offset=offset
    )
    data = [
        _progress(batch, await repo.progress(auth.organization_id, batch.id))
        for batch in batches
    ]
    return SuccessResponse(request_id=request_id, data=data)


@router.get(
    "/{batch_id}",
    response_model=SuccessResponse[BatchProgress],
    summary="Check a batch's progress",
)
async def get_batch(
    batch_id: str,
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[BatchProgress]:
    repo = BatchRepository(db)
    batch = await repo.get(auth.organization_id, batch_id)
    if batch is None:
        raise NotFoundError("No batch with that id exists in this organization.")
    counts = await repo.progress(auth.organization_id, batch_id)
    return SuccessResponse(request_id=request_id, data=_progress(batch, counts))
