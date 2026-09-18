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
from app.core.config import Settings
from app.core.errors import DocuParseError, ProviderUnavailableError
from app.core.logging import get_logger
from app.db.base import utcnow
from app.db.session import get_session_factory
from app.models import (
    Document,
    DocumentStatus,
    Extraction,
    ExtractionJob,
    ExtractionStatus,
    JobStatus,
    WebhookEvent,
)
from app.pipelines.invoice_pipeline import ExtractionPipeline, PipelineResult, headline_flags
from app.providers.base import DocumentAIProvider
from app.providers.registry import get_provider
from app.repositories.documents import DocumentRepository
from app.repositories.extractions import ExtractionRepository
from app.repositories.jobs import JobRepository
from app.repositories.organizations import OrganizationRepository
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
from app.services.webhooks import emit_event

logger = get_logger("docuparse.extraction")

# Bounded by the job's own max_attempts, so this can never loop forever (§34).
_RETRY_DELAYS_SECONDS = (30, 120, 600)


def _retry_delay(attempt: int) -> int:
    index = min(max(attempt - 1, 0), len(_RETRY_DELAYS_SECONDS) - 1)
    return _RETRY_DELAYS_SECONDS[index]


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
        #    the provider, and never consumes quota — but it is still a request
        #    the caller made, so it is still recorded. Without this, someone
        #    debugging "my uploads keep failing" sees an empty request log.
        try:
            file = validate_upload(
                upload.content,
                filename=upload.filename,
                max_size_bytes=settings.max_file_size_bytes,
                max_page_count=settings.max_page_count,
            )
        except DocuParseError as exc:
            await self._record_usage_out_of_band(
                auth=auth,
                endpoint=endpoint,
                request_id=request_id,
                status_code=exc.status_code,
                error_code=exc.code,
                billable=False,
                pages=0,
                duration_ms=int((utcnow() - started).total_seconds() * 1000),
            )
            raise

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

    # --- asynchronous path (§6) -----------------------------------------

    async def submit_document(
        self,
        *,
        auth: AuthContext,
        upload: UploadedFile,
        request_id: str,
        endpoint: str = "/v1/documents",
    ) -> ExtractionJob:
        """Accept a document for background processing.

        Returns as soon as the bytes are safely stored and a job row exists,
        so a 25-page scan does not hold an HTTP connection open for a minute.
        """
        settings = auth.settings
        now = utcnow()

        try:
            file = validate_upload(
                upload.content,
                filename=upload.filename,
                max_size_bytes=settings.max_file_size_bytes,
                max_page_count=settings.max_page_count,
            )
        except DocuParseError as exc:
            await self._record_usage_out_of_band(
                auth=auth, endpoint=endpoint, request_id=request_id,
                status_code=exc.status_code, error_code=exc.code,
                billable=False, pages=0,
            )
            raise

        # A missing provider is a configuration error, not a transient one:
        # queueing work that can never run would just defer the same 503 and
        # leave a job stuck in the customer's list. Building the provider is
        # what tells the two apart — it fails on configuration, never on
        # reachability, and an unreachable provider is the worker's problem.
        try:
            self._provider or get_provider()
        except ProviderUnavailableError:
            await self._record_usage_out_of_band(
                auth=auth, endpoint=endpoint, request_id=request_id,
                status_code=503, error_code="extraction_provider_unavailable",
                billable=False, pages=file.page_count,
            )
            raise

        document = await self._persist_document(
            auth, file, request_id=request_id, now=now, force_store=True
        )
        job = await JobRepository(self._db).create(
            organization_id=auth.organization_id,
            document_id=document.id,
            request_id=request_id,
            api_key_id=auth.api_key_id,
            max_attempts=settings.job_max_attempts,
        )

        await emit_event(
            self._db,
            organization_id=auth.organization_id,
            event=WebhookEvent.DOCUMENT_PROCESSING,
            job_id=job.id,
            document_id=document.id,
            status=JobStatus.QUEUED,
            settings=settings,
        )
        await UsageRepository(self._db).record(
            organization_id=auth.organization_id,
            api_key_id=auth.api_key_id,
            document_id=document.id,
            request_id=request_id,
            endpoint=endpoint,
            event_type=auth.usage_event_type,
            status_code=202,
            success=True,
            # Not billable yet: no provider call has happened. The worker
            # records the billable event when it actually does the work.
            billable=False,
            pages=file.page_count,
        )
        await self._db.commit()

        logger.info(
            "job.submitted",
            organization_id=auth.organization_id,
            job_id=job.id,
            document_id=document.id,
            pages=file.page_count,
        )
        return job

    async def process_job(self, job: ExtractionJob, *, settings: Settings) -> bool:
        """Run one claimed job. Returns True if it completed successfully.

        Never raises: a worker that dies on a bad document stops processing
        every other customer's documents too.
        """
        started = utcnow()
        organization = await OrganizationRepository(self._db).get(job.organization_id)
        document = await DocumentRepository(self._db).get(
            job.organization_id, job.document_id
        )
        if organization is None or document is None:
            await self._fail_job(
                job,
                error_code="document_unavailable",
                message="The document for this job no longer exists.",
                retryable=False,
                settings=settings,
            )
            return False

        auth = AuthContext(
            organization=organization, settings=settings, api_key=None, actor="worker"
        )

        if document.storage_key is None:
            await self._fail_job(
                job,
                error_code="document_unavailable",
                message=(
                    "The stored document was deleted before it could be processed."
                ),
                retryable=False,
                settings=settings,
            )
            return False

        try:
            provider = self._provider or get_provider()
            content = await (self._object_store or get_object_store()).get(
                document.storage_key
            )
            file = validate_upload(
                content,
                filename=document.filename,
                max_size_bytes=settings.max_file_size_bytes,
                max_page_count=settings.max_page_count,
            )
            result = await ExtractionPipeline(provider=provider, settings=settings).run(file)
        except DocuParseError as exc:
            await self._fail_job(
                job,
                error_code=exc.code,
                message=exc.message,
                # A provider outage or a storage blip is worth another go. A
                # document the model could not parse is not: the same bytes
                # and the same prompt produce the same answer.
                retryable=exc.status_code >= 500,
                settings=settings,
                auth=auth,
                pages=document.page_count,
                started=started,
            )
            return False
        except Exception as exc:  # noqa: BLE001 — the worker must survive
            logger.exception("job.unexpected_error", job_id=job.id)
            await self._fail_job(
                job,
                error_code="internal_error",
                message=f"An unexpected error occurred ({type(exc).__name__}).",
                retryable=True,
                settings=settings,
                auth=auth,
                pages=document.page_count,
                started=started,
            )
            return False

        extraction = await self._store_result(
            auth=auth,
            document_id=document.id,
            request_id=job.request_id or job.id,
            endpoint="/v1/documents",
            result=result,
        )
        await JobRepository(self._db).mark_completed(job, extraction_id=extraction.id)

        if auth.retention_days <= 0:
            # Process-and-delete: async needs the bytes long enough to read
            # them, so they are discarded the moment the work is done (§24).
            await self._purge_document_bytes(document)

        await emit_event(
            self._db,
            organization_id=job.organization_id,
            event=WebhookEvent.DOCUMENT_COMPLETED,
            job_id=job.id,
            document_id=document.id,
            status=JobStatus.COMPLETED,
            extraction_id=extraction.id,
            validation_overall=str(result.validation.overall),
            settings=settings,
        )
        await self._db.commit()

        logger.info(
            "job.completed",
            organization_id=job.organization_id,
            job_id=job.id,
            document_id=document.id,
            extraction_id=extraction.id,
            duration_ms=result.duration_ms,
        )
        return True

    async def _purge_document_bytes(self, document: Document) -> None:
        if document.storage_key is None:
            return
        try:
            await (self._object_store or get_object_store()).delete(document.storage_key)
        except Exception:  # noqa: BLE001 — the sweeper will retry
            logger.warning("job.purge_failed", document_id=document.id)
            return
        document.storage_key = None
        document.purged_at = utcnow()

    async def _fail_job(
        self,
        job: ExtractionJob,
        *,
        error_code: str,
        message: str,
        retryable: bool,
        settings: Settings,
        auth: AuthContext | None = None,
        pages: int = 0,
        started: dt.datetime | None = None,
    ) -> None:
        jobs = JobRepository(self._db)
        await jobs.mark_failed(
            job,
            error_code=error_code,
            error_message=message,
            retry_in_seconds=_retry_delay(job.attempts) if retryable else None,
        )
        terminal = job.status == JobStatus.FAILED

        if auth is not None:
            duration_ms = (
                int((utcnow() - started).total_seconds() * 1000) if started else None
            )
            await ExtractionRepository(self._db).create(
                organization_id=job.organization_id,
                document_id=job.document_id,
                request_id=job.request_id,
                status=ExtractionStatus.FAILED,
                error_code=error_code,
                error_message=message,
                total_latency_ms=duration_ms,
            )
            await UsageRepository(self._db).record(
                organization_id=job.organization_id,
                api_key_id=job.api_key_id,
                document_id=job.document_id,
                request_id=job.request_id,
                endpoint="/v1/documents",
                event_type="async_job",
                status_code=422 if error_code == "extraction_failed" else 500,
                success=False,
                # The attempt reached the provider, so it cost money.
                billable=error_code not in {"document_unavailable", "storage_error"},
                pages=pages,
                duration_ms=duration_ms,
                error_code=error_code,
            )

        if terminal:
            document = await DocumentRepository(self._db).get(
                job.organization_id, job.document_id
            )
            if document is not None:
                document.status = DocumentStatus.FAILED
            await emit_event(
                self._db,
                organization_id=job.organization_id,
                event=WebhookEvent.DOCUMENT_FAILED,
                job_id=job.id,
                document_id=job.document_id,
                status=JobStatus.FAILED,
                error_code=error_code,
                settings=settings,
            )

        await self._db.commit()
        logger.warning(
            "job.attempt_failed",
            organization_id=job.organization_id,
            job_id=job.id,
            error_code=error_code,
            attempt=job.attempts,
            terminal=terminal,
        )

    async def record_rejected_request(
        self,
        *,
        auth: AuthContext,
        request_id: str,
        error: DocuParseError,
        endpoint: str = "/v1/invoices/extract",
    ) -> None:
        """Log a request refused before the service could run.

        The upload reader rejects an oversized body mid-stream, in the route,
        so that path never reaches ``extract_invoice``. It is still a request
        the caller made and still belongs in their log.
        """
        await self._record_usage_out_of_band(
            auth=auth,
            endpoint=endpoint,
            request_id=request_id,
            status_code=error.status_code,
            error_code=error.code,
            billable=False,
            pages=0,
        )

    # --- steps ---------------------------------------------------------

    async def _persist_document(
        self,
        auth: AuthContext,
        file: ValidatedFile,
        *,
        request_id: str,
        now: dt.datetime,
        force_store: bool = False,
    ):
        """Store the document.

        ``force_store`` is the asynchronous path: a worker cannot read bytes
        that were never written, so process-and-delete organizations still get
        their document stored — and the worker deletes it the moment the job
        is done.
        """
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

        if retention_days > 0 or force_store:
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
        extraction = await self._store_result(
            auth=auth,
            document_id=document_id,
            request_id=request_id,
            endpoint=endpoint,
            result=result,
        )
        return self._build_response(
            auth=auth,
            request_id=request_id,
            document_id=document_id,
            extraction_id=extraction.id,
            result=result,
        )

    async def _store_result(
        self,
        *,
        auth: AuthContext,
        document_id: str,
        request_id: str,
        endpoint: str,
        result: PipelineResult,
    ) -> Extraction:
        """Persist a successful extraction and its usage.

        Shared by the synchronous route and the worker, so the two can never
        drift on what a successful extraction records.
        """
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
            prompt_version=result.prompt_version,
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
            api_key_id=auth.api_key_id,
            document_id=document_id,
            request_id=request_id,
            endpoint=endpoint,
            event_type=auth.usage_event_type,
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

        return extraction

    def _build_response(
        self,
        *,
        auth: AuthContext,
        request_id: str,
        document_id: str,
        extraction_id: str,
        result: PipelineResult,
    ) -> ExtractionResponse:
        confidence_payload = result.confidence.to_payload()
        flags = headline_flags(result.validation)
        return ExtractionResponse(
            request_id=request_id,
            document_id=document_id,
            extraction_id=extraction_id,
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
                tiers=result.tiers_used,
                model_called=result.model_called,
                escalation_reason=result.escalation_reason,
                notes=result.notes,
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
                api_key_id=auth.api_key_id,
                document_id=document_id,
                request_id=request_id,
                endpoint=endpoint,
                event_type=auth.usage_event_type,
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
        duration_ms: int | None = None,
    ) -> None:
        await self._db.commit()
        async with get_session_factory()() as session:
            await UsageRepository(session).record(
                organization_id=auth.organization_id,
                api_key_id=auth.api_key_id,
                request_id=request_id,
                endpoint=endpoint,
                event_type=auth.usage_event_type,
                status_code=status_code,
                success=False,
                billable=billable,
                pages=pages,
                error_code=error_code,
                estimated_cost_usd=estimated_cost_usd,
                duration_ms=duration_ms,
            )
            await session.commit()
