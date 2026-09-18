"""The extraction response (§1, §10)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.invoice import InvoiceData


class ConfidenceSummary(BaseModel):
    """Per-field confidence, plus one number for the whole document (§8)."""

    overall: float | None = Field(
        default=None,
        description="Weighted mean over the fields that matter for posting.",
    )
    band: Literal["high", "medium", "low"] | None = None
    fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Field path to {confidence, band, page, source_text}.",
    )
    low_confidence_fields: list[str] = Field(
        default_factory=list,
        description="Fields scoring below the medium threshold. Review these.",
    )


class ValidationCheck(BaseModel):
    name: str
    status: Literal["passed", "warning", "failed", "not_checked"]
    message: str | None = None
    details: dict[str, Any] | None = None


class ValidationSummary(BaseModel):
    overall: Literal["passed", "warning", "failed", "not_checked"]
    checks: list[ValidationCheck] = Field(default_factory=list)

    # The three headline booleans from the documented response example.
    # None means the check did not run — not that it passed.
    gstin_format_valid: bool | None = None
    calculation_matches: bool | None = None
    required_fields_present: bool | None = None


class ProcessingSummary(BaseModel):
    duration_ms: int
    pages: int
    provider: str
    model: str
    prompt_version: str
    tiers: list[str] = Field(
        default_factory=list,
        description=(
            "Which extraction tiers contributed, cheapest first: 'qr' (the "
            "e-invoice QR), 'text_layer' (the PDF's own characters), 'model'."
        ),
    )
    model_called: bool = Field(
        default=True,
        description="False when the cheaper tiers answered on their own.",
    )
    escalation_reason: str | None = Field(
        default=None,
        description="Why the model was needed, when it was.",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="What each tier did, and anything it deliberately left alone.",
    )
    provider_latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = Field(
        default=None,
        description=(
            "Our estimate from configured token rates, for your own cost "
            "tracking. Null when no rates are configured. Not a bill."
        ),
    )


class ExtractionResponse(BaseModel):
    """The full response body for a successful extraction."""

    success: Literal[True] = True
    request_id: str
    document_id: str
    extraction_id: str
    data: InvoiceData
    confidence: ConfidenceSummary
    validation: ValidationSummary
    processing: ProcessingSummary
