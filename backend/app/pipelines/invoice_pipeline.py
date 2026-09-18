"""The extraction pipeline (§11).

    FileValidation → Preprocess → Provider → Parse → Normalize
        → SchemaValidation → BusinessValidation → Confidence

Each stage is a separate callable and the provider arrives by injection, so
no stage knows what the one before it was implemented with. The route does
not call the model; it calls this.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

import anyio

from app.core.config import Settings
from app.core.logging import get_logger
from app.pipelines.stages.confidence import ConfidenceReport, configure_bands, score_extraction
from app.pipelines.stages.preprocess import PreparedDocument, prepare_document
from app.pipelines.strategies import DocumentTypeStrategy, get_strategy
from app.providers.base import DocumentAIProvider, ProviderUsage
from app.schemas.invoice import InvoiceData
from app.services.file_validation import ValidatedFile
from app.validators.base import CheckStatus, ValidationOutcome

logger = get_logger("docuparse.pipeline")


@dataclass
class PipelineResult:
    invoice: InvoiceData
    validation: ValidationOutcome
    confidence: ConfidenceReport
    pages: int
    provider_name: str
    model: str
    prompt_version: str
    provider_latency_ms: int
    duration_ms: int
    usage: ProviderUsage
    dropped_fields: list[str]
    normalized_fields: list[str]


class ExtractionPipeline:
    def __init__(
        self,
        *,
        provider: DocumentAIProvider,
        settings: Settings,
        strategy: DocumentTypeStrategy | None = None,
    ) -> None:
        self._provider = provider
        self._settings = settings
        self._strategy = strategy or get_strategy("gst_invoice")
        configure_bands(
            high=settings.confidence_high_threshold,
            medium=settings.confidence_medium_threshold,
        )

    @property
    def document_type(self) -> str:
        return self._strategy.document_type

    async def run(self, file: ValidatedFile) -> PipelineResult:
        started = time.perf_counter()
        strategy = self._strategy

        # Rendering a PDF is CPU-bound; keeping it off the event loop is what
        # lets one worker serve other requests while a 20-page scan renders.
        document: PreparedDocument = await anyio.to_thread.run_sync(
            prepare_document, file
        )

        structured = await self._provider.extract_structured_data(
            document,
            system_prompt=strategy.system_prompt,
            user_prompt=strategy.build_user_prompt(page_count=document.page_count),
        )

        parsed = strategy.parse(structured.data)
        invoice, normalization = strategy.normalize(parsed.invoice)

        validation = strategy.validate(
            invoice, rounding_tolerance=Decimal(self._settings.rounding_tolerance)
        )

        confidence = score_extraction(
            invoice,
            evidence=parsed.evidence,
            document_text=document.embedded_text,
            validation=validation,
            ambiguous_fields=parsed.ambiguous_fields,
        )

        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "pipeline.completed",
            document_type=strategy.document_type,
            pages=document.page_count,
            provider=self._provider.name,
            model=structured.model,
            provider_latency_ms=structured.latency_ms,
            duration_ms=duration_ms,
            validation_overall=str(validation.overall),
            overall_confidence=confidence.overall,
            dropped_field_count=len(parsed.dropped_fields),
        )

        return PipelineResult(
            invoice=invoice,
            validation=validation,
            confidence=confidence,
            pages=document.page_count,
            provider_name=self._provider.name,
            model=structured.model,
            prompt_version=strategy.prompt_version,
            provider_latency_ms=structured.latency_ms,
            duration_ms=duration_ms,
            usage=structured.usage,
            dropped_fields=parsed.dropped_fields,
            normalized_fields=normalization.changed_fields,
        )


def headline_flags(validation: ValidationOutcome) -> dict[str, bool | None]:
    """The three summary booleans in the documented response example.

    None where the underlying checks did not run — a B2C invoice with no
    buyer GSTIN must not report ``gstin_format_valid: false``.
    """

    def roll_up(names: tuple[str, ...]) -> bool | None:
        statuses = [
            check.status
            for check in validation.checks
            if check.name in names and check.status is not CheckStatus.NOT_CHECKED
        ]
        if not statuses:
            return None
        return all(status is CheckStatus.PASSED for status in statuses)

    return {
        "gstin_format_valid": roll_up(("supplier_gstin_format", "buyer_gstin_format")),
        "calculation_matches": roll_up(
            ("invoice_total", "taxable_amount_consistency", "line_item_calculation")
        ),
        "required_fields_present": roll_up(("required_fields_present",)),
    }
