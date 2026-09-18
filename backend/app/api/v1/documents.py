"""Document metadata and deletion (§24, §46).

A customer can see what we hold and delete it on demand. Deletion removes the
stored bytes immediately and keeps the metadata row, so usage history and
support lookups survive without retaining the document itself.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    enforce_document_quota,
    enforce_rate_limit,
    get_request_id,
)
from app.api.v1.uploads import read_upload
from app.core.errors import DocuParseError, InvalidRequestError, NotFoundError
from app.core.logging import get_logger
from app.db.base import utcnow
from app.db.session import get_db
from app.models import Document, DocumentStatus
from app.repositories.documents import DocumentRepository
from app.repositories.extractions import ExtractionRepository
from app.schemas.common import ErrorResponse, SuccessResponse
from app.schemas.jobs import JobAccepted
from app.services.extraction_service import ExtractionService, UploadedFile
from app.services.storage import get_object_store

router = APIRouter(prefix="/documents", tags=["documents"])
logger = get_logger("docuparse.documents")


class DocumentSummary(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    page_count: int
    document_type: str
    status: str
    request_id: str | None
    stored: bool
    retention_expires_at: dt.datetime | None
    purged_at: dt.datetime | None
    created_at: dt.datetime


def _summary(document: Document) -> DocumentSummary:
    return DocumentSummary(
        id=document.id,
        filename=document.filename,
        content_type=document.content_type,
        size_bytes=document.size_bytes,
        page_count=document.page_count,
        document_type=document.document_type,
        status=document.status,
        request_id=document.request_id,
        stored=document.storage_key is not None and document.purged_at is None,
        retention_expires_at=document.retention_expires_at,
        purged_at=document.purged_at,
        created_at=document.created_at,
    )


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAccepted,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        415: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    summary="Submit a document for asynchronous extraction",
    description=(
        "Accepts the upload and returns immediately with a job id. Poll "
        "`GET /v1/jobs/{job_id}`, or register a webhook and be told. Use this "
        "rather than `/v1/invoices/extract` for large or multi-page documents, "
        "so a slow extraction does not hold an HTTP connection open."
    ),
)
async def submit_document(
    file: UploadFile = File(..., description="The document: PDF, PNG, JPG or JPEG."),
    auth: AuthContext = Depends(enforce_document_quota),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> JobAccepted:
    if file is None or not file.filename:
        raise InvalidRequestError("No file was provided in the 'file' form field.")

    service = ExtractionService(db)
    try:
        content = await read_upload(
            file, max_size_bytes=auth.settings.max_file_size_bytes
        )
    except DocuParseError as exc:
        await service.record_rejected_request(
            auth=auth, request_id=request_id, error=exc, endpoint="/v1/documents"
        )
        raise

    job = await service.submit_document(
        auth=auth,
        upload=UploadedFile(content=content, filename=file.filename),
        request_id=request_id,
    )
    return JobAccepted(
        request_id=request_id,
        job_id=job.id,
        document_id=job.document_id,
        status=job.status,  # type: ignore[arg-type]
    )


@router.get("", response_model=SuccessResponse[list[DocumentSummary]], summary="List documents")
async def list_documents(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[DocumentSummary]]:
    documents = await DocumentRepository(db).list_for_organization(
        auth.organization_id, limit=limit, offset=offset
    )
    return SuccessResponse(request_id=request_id, data=[_summary(d) for d in documents])


@router.get(
    "/{document_id}",
    response_model=SuccessResponse[DocumentSummary],
    summary="Fetch one document's metadata",
)
async def get_document(
    document_id: str,
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[DocumentSummary]:
    document = await DocumentRepository(db).get(auth.organization_id, document_id)
    if document is None:
        # Another tenant's document is reported as absent, not forbidden.
        raise NotFoundError("No document with that id exists in this organization.")
    return SuccessResponse(request_id=request_id, data=_summary(document))


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_200_OK,
    response_model=SuccessResponse[DocumentSummary],
    summary="Delete a stored document immediately",
)
async def delete_document(
    document_id: str,
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[DocumentSummary]:
    repo = DocumentRepository(db)
    document = await repo.get(auth.organization_id, document_id)
    if document is None:
        raise NotFoundError("No document with that id exists in this organization.")

    if document.storage_key and document.purged_at is None:
        await get_object_store().delete(document.storage_key)
    document.purged_at = utcnow()
    document.storage_key = None
    document.status = DocumentStatus.PURGED
    await db.flush()
    logger.info(
        "document.purged",
        organization_id=auth.organization_id,
        document_id=document.id,
        reason="api_request",
    )
    return SuccessResponse(request_id=request_id, data=_summary(document))


class StoredExtraction(BaseModel):
    """A stored extraction, as the dashboard and the documents API return it."""

    id: str
    document_id: str
    request_id: str | None
    status: str
    document_type: str
    data: dict | None
    confidence: dict | None
    overall_confidence: float | None
    validation: dict | None
    provider: str | None
    model: str | None
    prompt_version: str | None
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: float | None
    provider_latency_ms: int | None
    total_latency_ms: int | None
    error_code: str | None
    error_message: str | None
    created_at: dt.datetime


@router.get(
    "/{document_id}/extraction",
    response_model=SuccessResponse[StoredExtraction],
    summary="The stored extraction result for a document",
)
async def get_document_extraction(
    document_id: str,
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[StoredExtraction]:
    documents = DocumentRepository(db)
    if await documents.get(auth.organization_id, document_id) is None:
        raise NotFoundError("No document with that id exists in this organization.")

    found = await ExtractionRepository(db).latest_for_document(
        auth.organization_id, document_id
    )
    if found is None:
        raise NotFoundError("That document has no extraction result yet.")

    extraction, validation = found
    return SuccessResponse(
        request_id=request_id,
        data=StoredExtraction(
            id=extraction.id,
            document_id=extraction.document_id,
            request_id=extraction.request_id,
            status=extraction.status,
            document_type=extraction.document_type,
            data=extraction.data,
            confidence=extraction.field_confidence,
            overall_confidence=extraction.overall_confidence,
            validation=(
                {"overall": validation.overall, "checks": validation.checks}
                if validation is not None
                else None
            ),
            provider=extraction.provider,
            model=extraction.model,
            prompt_version=extraction.prompt_version,
            input_tokens=extraction.input_tokens,
            output_tokens=extraction.output_tokens,
            estimated_cost_usd=float(extraction.estimated_cost_usd)
            if extraction.estimated_cost_usd is not None
            else None,
            provider_latency_ms=extraction.provider_latency_ms,
            total_latency_ms=extraction.total_latency_ms,
            error_code=extraction.error_code,
            error_message=extraction.error_message,
            created_at=extraction.created_at,
        ),
    )
