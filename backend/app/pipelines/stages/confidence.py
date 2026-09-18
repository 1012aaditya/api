"""Per-field confidence (§8).

The scores here are computed from evidence, not asserted by the model.
Asking a language model "how sure are you, 0 to 1?" produces a number that
looks calibrated and is not, which is precisely the fake precision §8
forbids. So confidence is a deterministic function of signals we can check:

* did the model cite the characters it read, and did it flag the read as
  uncertain?
* do those characters actually appear in the PDF's own text layer?
* does the value satisfy the format its field is defined to have?
* do the arithmetic and jurisdiction checks that cover this field agree?

The scale is documented and bounded. Nothing ever scores 1.0, because
nothing here is ever certain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.pipelines.stages.parse import FieldEvidence
from app.schemas.invoice import InvoiceData
from app.validators.base import CheckStatus, ValidationOutcome
from app.validators.gstin import PAN_PATTERN, is_valid_gstin

# Scoring constants. Tuned to be defensible rather than flattering.
#
# BASE_SCORE sits at the bottom of the MEDIUM band on purpose. A field the
# model returned with nothing corroborating it is "extracted but unverified",
# which is medium — not low. LOW is reserved for fields we have an actual
# reason to doubt: a failed format check, a citation absent from the text
# layer, a failed cross-check, or the model flagging its own uncertainty.
# Otherwise every field the prompt does not request evidence for would be
# flagged for review, and the list of things to review would be useless.
BASE_SCORE = 0.60
EVIDENCE_CITED_BONUS = 0.20
EVIDENCE_IN_TEXT_BONUS = 0.10
EVIDENCE_UNCERTAIN_PENALTY = 0.25
FORMAT_PASS_BONUS = 0.10
FORMAT_FAIL_PENALTY = 0.30
CROSS_CHECK_PASS_BONUS = 0.08
CROSS_CHECK_FAIL_PENALTY = 0.35
AMBIGUOUS_PENALTY = 0.10
MIN_SCORE = 0.05
MAX_SCORE = 0.97


class ConfidenceBand(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_HSN = re.compile(r"^\d{4}(\d{2})?(\d{2})?$")

# Which validation checks speak to which fields.
_CROSS_CHECKS: dict[str, tuple[str, ...]] = {
    "total": ("invoice_total",),
    "subtotal": ("invoice_total", "taxable_amount_consistency", "line_item_sum"),
    "tax.taxable_amount": ("invoice_total", "taxable_amount_consistency", "line_item_sum"),
    "tax.cgst": ("invoice_total", "cgst_sgst_split", "supply_type_consistency"),
    "tax.sgst": ("invoice_total", "cgst_sgst_split", "supply_type_consistency"),
    "tax.utgst": ("invoice_total", "supply_type_consistency"),
    "tax.igst": ("invoice_total", "supply_type_consistency"),
    "tax.cess": ("invoice_total",),
    "other_charges": ("invoice_total",),
    "round_off": ("invoice_total",),
    "discount": ("taxable_amount_consistency",),
    "supplier.gstin": ("supplier_gstin_format", "supply_type_consistency"),
    "buyer.gstin": ("buyer_gstin_format",),
    "place_of_supply": ("supply_type_consistency",),
}

# Fields that carry the overall score, and how much each is worth. These are
# the ones an integrator actually posts to a ledger.
_OVERALL_WEIGHTS: dict[str, float] = {
    "invoice_number": 2.0,
    "invoice_date": 2.0,
    "supplier.name": 1.0,
    "supplier.gstin": 1.5,
    "buyer.name": 1.0,
    "buyer.gstin": 1.0,
    "tax.taxable_amount": 1.5,
    "tax.cgst": 1.0,
    "tax.sgst": 1.0,
    "tax.igst": 1.0,
    "total": 2.5,
}


@dataclass(frozen=True)
class FieldConfidence:
    path: str
    confidence: float
    band: ConfidenceBand
    evidence_text: str | None = None
    evidence_page: int | None = None
    signals: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"confidence": self.confidence, "band": str(self.band)}
        if self.evidence_page is not None:
            payload["page"] = self.evidence_page
        if self.evidence_text:
            payload["source_text"] = self.evidence_text
        return payload


@dataclass
class ConfidenceReport:
    fields: dict[str, FieldConfidence] = field(default_factory=dict)
    overall: float | None = None

    def to_payload(self) -> dict[str, Any]:
        return {path: fc.to_payload() for path, fc in self.fields.items()}

    @property
    def overall_band(self) -> ConfidenceBand | None:
        return band_for(self.overall) if self.overall is not None else None

    def low_confidence_fields(self, threshold: float) -> list[str]:
        return sorted(p for p, fc in self.fields.items() if fc.confidence < threshold)


_high_threshold = 0.85
_medium_threshold = 0.60


def configure_bands(*, high: float, medium: float) -> None:
    global _high_threshold, _medium_threshold
    _high_threshold, _medium_threshold = high, medium


def band_for(score: float) -> ConfidenceBand:
    if score >= _high_threshold:
        return ConfidenceBand.HIGH
    if score >= _medium_threshold:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW


def _format_verdict(path: str, value: Any) -> bool | None:
    """True/False when the field has a defined format, None when it does not."""
    leaf = path.rsplit(".", 1)[-1]
    if leaf == "gstin":
        return is_valid_gstin(str(value))
    if leaf == "pan":
        return bool(PAN_PATTERN.match(str(value).upper()))
    if leaf == "email":
        return bool(_EMAIL.match(str(value)))
    if leaf == "ifsc":
        return bool(_IFSC.match(str(value).upper()))
    if leaf == "hsn_sac":
        return bool(_HSN.match(str(value)))
    return None


def _normalise_for_match(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _evidence_supported(evidence_text: str | None, document_text: str | None) -> bool | None:
    """Did the cited characters really appear in the document's text layer?

    None means we could not tell — a scanned PDF or an image has no text
    layer to check against, and absence of proof is not proof of absence.
    """
    if not evidence_text or not document_text:
        return None
    haystack = _normalise_for_match(document_text)
    needle = _normalise_for_match(evidence_text)
    if not needle:
        return None
    return needle in haystack


def _cross_check_signal(path: str, validation: ValidationOutcome | None) -> str | None:
    if validation is None:
        return None
    names = _CROSS_CHECKS.get(path)
    if not names:
        return None
    statuses = {
        check.status
        for check in validation.checks
        if check.name in names and check.status is not CheckStatus.NOT_CHECKED
    }
    if not statuses:
        return None
    if CheckStatus.FAILED in statuses:
        return "failed"
    if CheckStatus.WARNING in statuses:
        return "warning"
    return "passed"


def _score(
    path: str,
    value: Any,
    *,
    evidence: FieldEvidence | None,
    document_text: str | None,
    validation: ValidationOutcome | None,
    ambiguous: bool,
) -> FieldConfidence:
    score = BASE_SCORE
    signals: dict[str, Any] = {}

    if evidence is not None and evidence.text:
        score += EVIDENCE_CITED_BONUS
        signals["cited"] = True
        supported = _evidence_supported(evidence.text, document_text)
        if supported is True:
            score += EVIDENCE_IN_TEXT_BONUS
            signals["found_in_text_layer"] = True
        elif supported is False:
            # The model cited characters the text layer does not contain.
            score -= FORMAT_FAIL_PENALTY
            signals["found_in_text_layer"] = False
        if not evidence.certain:
            score -= EVIDENCE_UNCERTAIN_PENALTY
            signals["model_uncertain"] = True
    else:
        signals["cited"] = False

    format_ok = _format_verdict(path, value)
    if format_ok is True:
        score += FORMAT_PASS_BONUS
        signals["format_valid"] = True
    elif format_ok is False:
        score -= FORMAT_FAIL_PENALTY
        signals["format_valid"] = False

    cross = _cross_check_signal(path, validation)
    if cross == "passed":
        score += CROSS_CHECK_PASS_BONUS
    elif cross == "warning":
        score -= CROSS_CHECK_PASS_BONUS
    elif cross == "failed":
        score -= CROSS_CHECK_FAIL_PENALTY
    if cross:
        signals["cross_check"] = cross

    if ambiguous:
        score -= AMBIGUOUS_PENALTY
        signals["ambiguous_source"] = True

    score = round(max(MIN_SCORE, min(MAX_SCORE, score)), 3)
    return FieldConfidence(
        path=path,
        confidence=score,
        band=band_for(score),
        evidence_text=evidence.text if evidence else None,
        evidence_page=evidence.page if evidence else None,
        signals=signals,
    )


def _leaf_paths(payload: Any, prefix: str = "") -> list[tuple[str, Any]]:
    leaves: list[tuple[str, Any]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "document_type":
                continue
            leaves.extend(_leaf_paths(value, f"{prefix}.{key}" if prefix else key))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            leaves.extend(_leaf_paths(value, f"{prefix}[{index}]"))
    elif payload is not None:
        leaves.append((prefix, payload))
    return leaves


def score_extraction(
    invoice: InvoiceData,
    *,
    evidence: dict[str, FieldEvidence],
    document_text: str | None = None,
    validation: ValidationOutcome | None = None,
    ambiguous_fields: tuple[str, ...] | list[str] = (),
) -> ConfidenceReport:
    ambiguous = set(ambiguous_fields)
    report = ConfidenceReport()

    for path, value in _leaf_paths(invoice.model_dump(mode="json")):
        if isinstance(value, Decimal):
            value = float(value)
        report.fields[path] = _score(
            path,
            value,
            evidence=evidence.get(path),
            document_text=document_text,
            validation=validation,
            ambiguous=path in ambiguous,
        )

    weighted_sum = 0.0
    weight_total = 0.0
    for path, weight in _OVERALL_WEIGHTS.items():
        confidence = report.fields.get(path)
        if confidence is not None:
            weighted_sum += confidence.confidence * weight
            weight_total += weight
    report.overall = round(weighted_sum / weight_total, 3) if weight_total else None
    return report
