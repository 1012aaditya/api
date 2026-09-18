"""Document metadata and deletion (§24, §46).

A customer can see what we hold and delete it on demand. Deletion removes the
stored bytes immediately and keeps the metadata row, so usage history and
support lookups survive without retaining the document itself.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, enforce_rate_limit, get_request_id
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.db.base import utcnow
from app.db.session import get_db
from app.models import Document, DocumentStatus
from app.repositories.documents import DocumentRepository
from app.schemas.common import SuccessResponse
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
