"""Ties the pipeline to persistence, usage accounting and the error contract.

The route stays thin: it parses the upload and hands off here. This module
owns the ordering that matters —

* the provider is checked before any bytes are stored, so an unconfigured
  deployment fails fast and stores nothing;
* usage is recorded whether the request succeeds or fails, because a failed
  request that reached the provider still cost money (§21, §34);
* a failure is persisted as a failed extraction, not swallowed.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext
from app.core.errors import DocuParseError, ProviderUnavailableError
from app.core.logging import get_logger
from app.db.base import utcnow
from app.db.session import get_session_factory
from app.models import DocumentStatus, ExtractionStatus
from app.pipelines.invoice_pipeline import ExtractionPipeline, PipelineResult, headline_flags
from app.providers.base import DocumentAIProvider
from app.providers.registry import get_provider
from app.repositories.documents import DocumentRepository
from app.repositories.extractions import ExtractionRepository
from app.repositories.usage import UsageRepository
from app.schemas.extraction import (
    ConfidenceSummary,
    ExtractionResponse,
    ProcessingSummary,
    ValidationCheck,
    ValidationSummary,
)
from app.services.file_validation import ValidatedFile, validate_upload
from app.services.storage import ObjectStore, build_storage_key, get_object_store

logger = get_logger("docuparse.extraction")


@dataclass
class UploadedFile:
    content: bytes
    filename: str


class ExtractionService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        object_store: ObjectStore | None = None,
        provider: DocumentAIProvider | None = None,
    ) -> None:
        self._db = db
        self._object_store = object_store
        self._provider = provider

    async def extract_invoice(
        self,
        *,
        auth: AuthContext,
        upload: UploadedFile,
        request_id: str,
        endpoint: str = "/v1/invoices/extract",
    ) -> ExtractionResponse:
        settings = auth.settings
        started = utcnow()

        # 1. Cheap, local checks first. A bad file never reaches storage or
        #    the provider, and never consumes quota.
        file = validate_upload(
            upload.content,
            filename=upload.filename,
            max_size_bytes=settings.max_file_size_bytes,
            max_page_count=settings.max_page_count,
        )

        # 2. Refuse early if extraction cannot actually happen (§42).
        try:
            provider = self._provider or get_provider()
        except ProviderUnavailableError:
            await self._record_usage_out_of_band(
                auth=auth,
                endpoint=endpoint,
                request_id=request_id,
                status_code=503,
                error_code="extraction_provider_unavailable",
                billable=False,
                pages=file.page_count,
            )
            raise

        # 3. Store the bytes, unless the organization runs process-and-delete.
        document = await self._persist_document(auth, file, request_id=request_id, now=started)

        pipeline = ExtractionPipeline(provider=provider, settings=settings)
        try:
            result = await pipeline.run(file)
        except DocuParseError as exc:
            await self._on_failure(
                auth=auth,
                document_id=document.id,
                request_id=request_id,
                endpoint=endpoint,
                error=exc,
                pages=file.page_count,
                provider=provider,
                started=started,
            )
            raise
        except Exception as exc:
            # An unexpected exception must not leave the document stuck in
            # "processing" with no extraction row and no usage event. Record
            # it as an internal failure, then let the original propagate so
            # the handler still logs the real traceback.
            logger.exception("extraction.unexpected_error", document_id=document.id)
            await self._on_failure(
                auth=auth,
                document_id=document.id,
                request_id=request_id,
                endpoint=endpoint,
                error=DocuParseError(
                    "An unexpected error occurred while extracting this document."
                ),
                pages=file.page_count,
                provider=provider,
                started=started,
            )
            raise exc

        return await self._on_success(
            auth=auth,
            document_id=document.id,
            request_id=request_id,
            endpoint=endpoint,
            result=result,
        )

    # --- steps ---------------------------------------------------------

    async def _persist_document(
        self, auth: AuthContext, file: ValidatedFile, *, request_id: str, now: dt.datetime
    ):
        retention_days = auth.retention_days
        storage_key: str | None = None
        store = self._object_store or get_object_store()

        documents = DocumentRepository(self._db)
        document = await documents.create(
            organization_id=auth.organization_id,
            filename=file.filename,
            content_type=file.content_type,
            size_bytes=file.size_bytes,
            page_count=file.page_count,
            checksum_sha256=file.checksum_sha256,
            storage_backend=store.name,
            storage_key=None,
            request_id=request_id,
            retention_expires_at=(
                now + dt.timedelta(days=retention_days) if retention_days > 0 else None
            ),
        )

        if retention_days > 0:
            storage_key = build_storage_key(
                organization_id=auth.organization_id,
                document_id=document.id,
                content_type=file.content_type,
                now=now,
            )
            await store.put(storage_key, file.content, content_type=file.content_type)
            document.storage_key = storage_key
        else:
            # DOCUMENT_RETENTION_DAYS=0 is "process and delete" (§24): the
            # bytes are never written down at all.
            logger.info("document.not_stored_by_policy", document_id=document.id)

        document.status = DocumentStatus.PROCESSING
        # Commit now, before the provider call. Two reasons: the row survives
        # a crash mid-pipeline, and the request stops holding write locks that
        # the out-of-band failure writer would otherwise block on.
        await self._db.commit()
        return document

    async def _on_success(
        self,
        *,
        auth: AuthContext,
        document_id: str,
        request_id: str,
        endpoint: str,
        result: PipelineResult,
    ) -> ExtractionResponse:
        invoice_payload = result.invoice.model_dump(mode="json")
        confidence_payload = result.confidence.to_payload()

        extraction = await ExtractionRepository(self._db).create(
            organization_id=auth.organization_id,
            document_id=document_id,
            request_id=request_id,
            status=ExtractionStatus.SUCCEEDED,
            data=invoice_payload,
            field_confidence=confidence_payload,
            overall_confidence=result.confidence.overall,
            provider=result.provider_name,
            model=result.model,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            estimated_cost_usd=result.usage.estimated_cost_usd,
            provider_latency_ms=result.provider_latency_ms,
            total_latency_ms=result.duration_ms,
        )
        await ExtractionRepository(self._db).save_validation(
            organization_id=auth.organization_id,
            extraction_id=extraction.id,
            overall=str(result.validation.overall),
            checks=[check.to_payload() for check in result.validation.checks],
        )

        document = await DocumentRepository(self._db).get(auth.organization_id, document_id)
        if document is not None:
            document.status = DocumentStatus.COMPLETED

        await UsageRepository(self._db).record(
            organization_id=auth.organization_id,
            api_key_id=auth.api_key.id,
            document_id=document_id,
            request_id=request_id,
            endpoint=endpoint,
            status_code=200,
            success=True,
            billable=True,
            pages=result.pages,
            provider=result.provider_name,
            model=result.model,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            estimated_cost_usd=result.usage.estimated_cost_usd,
            duration_ms=result.duration_ms,
        )

        flags = headline_flags(result.validation)
        return ExtractionResponse(
            request_id=request_id,
            document_id=document_id,
            extraction_id=extraction.id,
            data=result.invoice,
            confidence=ConfidenceSummary(
                overall=result.confidence.overall,
                band=str(result.confidence.overall_band)
                if result.confidence.overall_band
                else None,
                fields=confidence_payload,
                low_confidence_fields=result.confidence.low_confidence_fields(
                    auth.settings.confidence_medium_threshold
                ),
            ),
            validation=ValidationSummary(
                overall=str(result.validation.overall),
                checks=[
                    ValidationCheck(**check.to_payload()) for check in result.validation.checks
                ],
                **flags,
            ),
            processing=ProcessingSummary(
                duration_ms=result.duration_ms,
                pages=result.pages,
                provider=result.provider_name,
                model=result.model,
                prompt_version=result.prompt_version,
                provider_latency_ms=result.provider_latency_ms,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                estimated_cost_usd=float(result.usage.estimated_cost_usd)
                if result.usage.estimated_cost_usd is not None
                else None,
            ),
        )

    async def _on_failure(
        self,
        *,
        auth: AuthContext,
        document_id: str,
        request_id: str,
        endpoint: str,
        error: DocuParseError,
        pages: int,
        provider: DocumentAIProvider,
        started: dt.datetime,
    ) -> None:
        duration_ms = int((utcnow() - started).total_seconds() * 1000)
        logger.warning(
            "extraction.failed",
            document_id=document_id,
            error_code=error.code,
            provider=provider.name,
            model=provider.model,
        )
        # The request's own session is about to roll back, so the failure
        # record and the usage event are written on a fresh one. Commit this
        # session's pending work first — otherwise the new session blocks on
        # locks the old one still holds, and the two deadlock.
        await self._db.commit()
        async with get_session_factory()() as session:
            await ExtractionRepository(session).create(
                organization_id=auth.organization_id,
                document_id=document_id,
                request_id=request_id,
                status=ExtractionStatus.FAILED,
                provider=provider.name,
                model=provider.model,
                error_code=error.code,
                error_message=error.message,
                total_latency_ms=duration_ms,
            )
            document = await DocumentRepository(session).get(
                auth.organization_id, document_id
            )
            if document is not None:
                document.status = DocumentStatus.FAILED
            await UsageRepository(session).record(
                organization_id=auth.organization_id,
                api_key_id=auth.api_key.id,
                document_id=document_id,
                request_id=request_id,
                endpoint=endpoint,
                status_code=error.status_code,
                success=False,
                # The provider was called and billed us, so this consumes quota
                # even though the caller got an error.
                billable=error.status_code not in (400, 413, 415),
                pages=pages,
                provider=provider.name,
                model=provider.model,
                duration_ms=duration_ms,
                error_code=error.code,
            )
            await session.commit()

    async def _record_usage_out_of_band(
        self,
        *,
        auth: AuthContext,
        endpoint: str,
        request_id: str,
        status_code: int,
        error_code: str,
        billable: bool,
        pages: int,
        estimated_cost_usd: Decimal | None = None,
    ) -> None:
        await self._db.commit()
        async with get_session_factory()() as session:
            await UsageRepository(session).record(
                organization_id=auth.organization_id,
                api_key_id=auth.api_key.id,
                request_id=request_id,
                endpoint=endpoint,
                status_code=status_code,
                success=False,
                billable=billable,
                pages=pages,
                error_code=error_code,
                estimated_cost_usd=estimated_cost_usd,
            )
            await session.commit()
