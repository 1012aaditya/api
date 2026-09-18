from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Extraction, ValidationResult


class ExtractionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, organization_id: str, extraction_id: str) -> Extraction | None:
        result = await self.session.execute(
            select(Extraction).where(
                Extraction.id == extraction_id,
                Extraction.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        organization_id: str,
        document_id: str,
        request_id: str | None,
        status: str,
        document_type: str = "gst_invoice",
        data: dict[str, Any] | None = None,
        field_confidence: dict[str, Any] | None = None,
        overall_confidence: float | None = None,
        provider: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        estimated_cost_usd: Decimal | None = None,
        provider_latency_ms: int | None = None,
        total_latency_ms: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> Extraction:
        extraction = Extraction(
            organization_id=organization_id,
            document_id=document_id,
            request_id=request_id,
            status=status,
            document_type=document_type,
            data=data,
            field_confidence=field_confidence,
            overall_confidence=overall_confidence,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated_cost_usd,
            provider_latency_ms=provider_latency_ms,
            total_latency_ms=total_latency_ms,
            error_code=error_code,
            error_message=error_message,
        )
        self.session.add(extraction)
        await self.session.flush()
        return extraction

    async def save_validation(
        self,
        *,
        organization_id: str,
        extraction_id: str,
        overall: str,
        checks: list[dict[str, Any]],
    ) -> ValidationResult:
        row = ValidationResult(
            organization_id=organization_id,
            extraction_id=extraction_id,
            overall=overall,
            checks=checks,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def latest_for_document(
        self, organization_id: str, document_id: str
    ) -> tuple[Extraction, ValidationResult | None] | None:
        """The most recent extraction attempt for a document, with its checks."""
        result = await self.session.execute(
            select(Extraction)
            .where(
                Extraction.organization_id == organization_id,
                Extraction.document_id == document_id,
            )
            .order_by(Extraction.created_at.desc())
            .limit(1)
        )
        extraction = result.scalar_one_or_none()
        if extraction is None:
            return None

        validation = (
            await self.session.execute(
                select(ValidationResult).where(
                    ValidationResult.organization_id == organization_id,
                    ValidationResult.extraction_id == extraction.id,
                )
            )
        ).scalar_one_or_none()
        return extraction, validation

    async def iter_for_export(
        self,
        organization_id: str,
        *,
        since: dt.datetime | None = None,
        until: dt.datetime | None = None,
        batch_id: str | None = None,
        only_succeeded: bool = True,
        page_size: int = 200,
    ):
        """Yield (extraction, document, validation) in pages.

        Paged rather than loaded whole: an export of a year's invoices should
        not have to fit in memory before the first byte reaches the client.
        """
        from app.models import Document, ValidationResult

        offset = 0
        while True:
            query = (
                select(Extraction, Document, ValidationResult)
                .join(Document, Document.id == Extraction.document_id)
                .outerjoin(
                    ValidationResult, ValidationResult.extraction_id == Extraction.id
                )
                .where(Extraction.organization_id == organization_id)
                .order_by(Extraction.created_at.desc())
                .limit(page_size)
                .offset(offset)
            )
            if only_succeeded:
                query = query.where(Extraction.status == "succeeded")
            if since is not None:
                query = query.where(Extraction.created_at >= since)
            if until is not None:
                query = query.where(Extraction.created_at <= until)
            if batch_id is not None:
                query = query.where(Document.batch_id == batch_id)

            rows = (await self.session.execute(query)).all()
            if not rows:
                return
            for row in rows:
                yield row[0], row[1], row[2]
            if len(rows) < page_size:
                return
            offset += page_size
