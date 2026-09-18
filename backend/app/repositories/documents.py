from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document


class DocumentRepository:
    """Every read is scoped to one organization (§16)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, organization_id: str, document_id: str) -> Document | None:
        result = await self.session.execute(
            select(Document).where(
                Document.id == document_id, Document.organization_id == organization_id
            )
        )
        return result.scalar_one_or_none()

    async def list_for_organization(
        self, organization_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[Document]:
        result = await self.session.execute(
            select(Document)
            .where(Document.organization_id == organization_id)
            .order_by(Document.created_at.desc())
            .limit(min(limit, 200))
            .offset(offset)
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        organization_id: str,
        filename: str,
        content_type: str,
        size_bytes: int,
        page_count: int,
        checksum_sha256: str,
        storage_backend: str,
        storage_key: str | None,
        request_id: str | None,
        document_type: str = "gst_invoice",
        retention_expires_at: dt.datetime | None = None,
    ) -> Document:
        document = Document(
            organization_id=organization_id,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            page_count=page_count,
            checksum_sha256=checksum_sha256,
            storage_backend=storage_backend,
            storage_key=storage_key,
            request_id=request_id,
            document_type=document_type,
            retention_expires_at=retention_expires_at,
        )
        self.session.add(document)
        await self.session.flush()
        return document

    async def set_status(self, document: Document, status: str) -> Document:
        document.status = status
        await self.session.flush()
        return document

    async def list_expired(self, *, now: dt.datetime, limit: int = 200) -> list[Document]:
        """Documents whose stored bytes are past their retention window (§24).

        Deliberately not organization-scoped: this serves the retention
        sweeper, which acts across all tenants and returns no data to a caller.
        """
        result = await self.session.execute(
            select(Document)
            .where(
                Document.retention_expires_at.is_not(None),
                Document.retention_expires_at <= now,
                Document.purged_at.is_(None),
                Document.storage_key.is_not(None),
            )
            .order_by(Document.retention_expires_at)
            .limit(limit)
        )
        return list(result.scalars().all())
