"""The extraction pipeline (§11, §34).

    FileValidation → Preprocess → [tier 0: QR] → [tier 1: text layer]
        → [tier 2: model, only if needed] → Merge → Normalize
        → SchemaValidation → BusinessValidation → Confidence

The tiers are ordered by how directly they read the document, and the router
escalates only when what it already has is not good enough — judged by the
validation engine, not by a guess. Most digital invoices never reach the
model; where one does, the cheap tiers still supply an independent reading to
check it against, which is a correctness win and not only a cost one.

Each stage is a separate callable and the provider arrives by injection, so
no stage knows what the one before it was implemented with. The route does
not call the model; it calls this.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal

import anyio

from app.core.config import Settings
from app.core.errors import ProviderUnavailableError
from app.core.logging import get_logger
from app.pipelines.merge import MergedExtraction, merge_tiers
from app.pipelines.stages.confidence import ConfidenceReport, configure_bands, score_extraction
from app.pipelines.stages.preprocess import PreparedDocument, prepare_document
from app.pipelines.stages.qr_extract import extract_from_qr
from app.pipelines.stages.text_extract import extract_from_text_layer
from app.pipelines.strategies import DocumentTypeStrategy, get_strategy
from app.pipelines.tiers import ExtractionTier, TierOutput
from app.providers.base import DocumentAIProvider, ProviderUsage
from app.providers.registry import get_provider
from app.schemas.invoice import InvoiceData
from app.services.file_validation import ValidatedFile
from app.validators.base import CheckStatus, ValidationOutcome
from app.validators.checks import check_source_agreement

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
    tiers_used: list[str] = field(default_factory=list)
    model_called: bool = True
    escalation_reason: str | None = None
    notes: list[str] = field(default_factory=list)


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
        settings = self._settings
        tiers = settings.enabled_tiers

        # Rendering a PDF is CPU-bound; keeping it off the event loop is what
        # lets one worker serve other requests while a 20-page scan renders.
        document: PreparedDocument = await anyio.to_thread.run_sync(
            prepare_document, file
        )

        outputs: list[TierOutput] = []
        if ExtractionTier.QR in tiers:
            qr_output = await anyio.to_thread.run_sync(extract_from_qr, file)
            if qr_output is not None:
                outputs.append(qr_output)
        if ExtractionTier.TEXT_LAYER in tiers:
            text_output = extract_from_text_layer(document)
            if text_output is not None:
                outputs.append(text_output)

        merged = merge_tiers(outputs)
        escalation = self._escalation_reason(merged)

        provider_name = "none"
        model_name = "none"
        provider_latency_ms = 0
        usage = ProviderUsage()
        dropped: list[str] = []
        ambiguous: list[str] = []
        model_called = False

        if escalation is not None and ExtractionTier.MODEL in tiers:
            provider = self._provider or get_provider()
            structured = await provider.extract_structured_data(
                document,
                system_prompt=strategy.system_prompt,
                user_prompt=strategy.build_user_prompt(page_count=document.page_count),
            )
            parsed = strategy.parse(structured.data)
            outputs.append(
                TierOutput(
                    tier=ExtractionTier.MODEL,
                    invoice=parsed.invoice,
                    evidence=parsed.evidence,
                    populated_paths=set(),
                )
            )
            merged = merge_tiers(outputs)
            provider_name = provider.name
            model_name = structured.model
            provider_latency_ms = structured.latency_ms
            usage = structured.usage
            dropped = parsed.dropped_fields
            ambiguous = parsed.ambiguous_fields
            model_called = True
        elif escalation is not None:
            # The cheap tiers fell short and the model tier is switched off.
            # The gaps are reported as missing, not filled in (§42).
            merged.notes.append(
                f"{escalation} The model tier is disabled on this deployment, so "
                "the missing fields are reported rather than inferred."
            )

        if not outputs:
            raise ProviderUnavailableError(
                "No extraction tier could read this document. Enable the model "
                "tier, or supply a document with a text layer or an e-invoice QR."
            )

        invoice, normalization = strategy.normalize(merged.invoice)
        validation = strategy.validate(
            invoice, rounding_tolerance=Decimal(settings.rounding_tolerance)
        )
        if len(merged.tiers_used) > 1:
            validation = ValidationOutcome(
                overall=validation.overall,
                checks=[
                    *validation.checks,
                    check_source_agreement(
                        [conflict.to_payload() for conflict in merged.conflicts]
                    ),
                ],
            )

        confidence = score_extraction(
            invoice,
            evidence=merged.evidence,
            document_text=document.embedded_text,
            validation=validation,
            ambiguous_fields=[*ambiguous, *merged.conflicting_paths],
        )

        duration_ms = int((time.perf_counter() - started) * 1000)
        tiers_used = [str(tier) for tier in merged.tiers_used]
        logger.info(
            "pipeline.completed",
            document_type=strategy.document_type,
            pages=document.page_count,
            tiers=tiers_used,
            model_called=model_called,
            escalation_reason=escalation,
            provider=provider_name,
            model=model_name,
            provider_latency_ms=provider_latency_ms,
            duration_ms=duration_ms,
            validation_overall=str(validation.overall),
            overall_confidence=confidence.overall,
            conflicts=len(merged.conflicts),
            dropped_field_count=len(dropped),
        )

        return PipelineResult(
            invoice=invoice,
            validation=validation,
            confidence=confidence,
            pages=document.page_count,
            provider_name=provider_name,
            model=model_name,
            prompt_version=strategy.prompt_version if model_called else "n/a",
            provider_latency_ms=provider_latency_ms,
            duration_ms=duration_ms,
            usage=usage,
            dropped_fields=dropped,
            normalized_fields=normalization.changed_fields,
            tiers_used=tiers_used,
            model_called=model_called,
            escalation_reason=escalation,
            notes=merged.notes,
        )

    def _escalation_reason(self, merged: MergedExtraction) -> str | None:
        """Why the cheap tiers are not enough, or None if they are.

        Deliberately explicit rather than a score: an integrator debugging why
        a document cost them a model call deserves a sentence, not a number.
        """
        invoice = merged.invoice
        if not merged.tiers_used:
            return "No cheap tier could read anything from this document."

        missing = [
            name
            for name, present in (
                ("invoice number", bool(invoice.invoice_number)),
                ("invoice date", invoice.invoice_date is not None),
                ("supplier", not invoice.supplier.is_empty()),
                ("total", invoice.total is not None),
            )
            if not present
        ]
        if missing:
            return f"The cheap tiers did not find: {', '.join(missing)}."

        if self._settings.extraction_require_line_items and not invoice.items:
            return (
                "No line items were read. The qr and text_layer tiers do not "
                "extract tables; set EXTRACTION_REQUIRE_LINE_ITEMS=false if "
                "header-level data is enough for your workflow."
            )

        totals = self._settings.rounding_tolerance
        outcome = self._strategy.validate(invoice, rounding_tolerance=Decimal(totals))
        if outcome.failed_names:
            return (
                "What the cheap tiers read does not reconcile: "
                f"{', '.join(outcome.failed_names)}."
            )
        return None


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
