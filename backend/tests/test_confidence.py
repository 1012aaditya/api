"""Confidence scoring (§8).

The contract these tests hold the scorer to: confidence must move with real
evidence, and must never claim certainty.
"""

from __future__ import annotations

from decimal import Decimal

from app.pipelines.stages.confidence import MAX_SCORE, band_for, score_extraction
from app.pipelines.stages.parse import FieldEvidence
from app.schemas.invoice import InvoiceData
from app.validators.engine import validate_invoice
from tests.fixtures.invoices import INVALID_GSTIN, SUPPLIER_GSTIN_KA

TEXT_LAYER = (
    "TAX INVOICE Invoice No: INV-29381 Date: 18/09/2026 "
    "GSTIN: 29AABCU9603R1ZJ Grand Total 1,18,000.00"
)

BASE = {
    "invoice_number": "INV-29381",
    "invoice_date": "2026-09-18",
    "place_of_supply": "29-Karnataka",
    "supplier": {"name": "Udupi Software Systems Pvt Ltd", "gstin": SUPPLIER_GSTIN_KA},
    "buyer": {"name": "Marigold Retail Pvt Ltd"},
    "subtotal": 100000,
    "total": 118000,
    "tax": {"taxable_amount": 100000, "cgst": 9000, "sgst": 9000},
}


def score(payload: dict, evidence: dict | None = None, text: str | None = TEXT_LAYER):
    invoice = InvoiceData.model_validate(payload)
    validation = validate_invoice(invoice, rounding_tolerance=Decimal("1"))
    return score_extraction(
        invoice,
        evidence=evidence or {},
        document_text=text,
        validation=validation,
    )


def test_nothing_is_ever_fully_certain() -> None:
    report = score(
        BASE,
        {
            "invoice_number": FieldEvidence("INV-29381", 1, True),
            "supplier.gstin": FieldEvidence(SUPPLIER_GSTIN_KA, 1, True),
            "total": FieldEvidence("1,18,000.00", 1, True),
        },
    )
    assert all(fc.confidence <= MAX_SCORE for fc in report.fields.values())
    assert MAX_SCORE < 1.0
    assert report.overall is not None and report.overall < 1.0


def test_cited_evidence_raises_confidence() -> None:
    uncited = score(BASE).fields["invoice_number"].confidence
    cited = score(BASE, {"invoice_number": FieldEvidence("INV-29381", 1, True)}).fields[
        "invoice_number"
    ].confidence
    assert cited > uncited


def test_a_citation_absent_from_the_text_layer_lowers_confidence() -> None:
    real = score(BASE, {"invoice_number": FieldEvidence("INV-29381", 1, True)})
    invented = score(BASE, {"invoice_number": FieldEvidence("INV-00000", 1, True)})
    assert invented.fields["invoice_number"].confidence < real.fields[
        "invoice_number"
    ].confidence
    assert invented.fields["invoice_number"].signals["found_in_text_layer"] is False


def test_a_model_flagged_uncertainty_lowers_confidence() -> None:
    certain = score(BASE, {"invoice_number": FieldEvidence("INV-29381", 1, True)})
    unsure = score(BASE, {"invoice_number": FieldEvidence("INV-29381", 1, False)})
    assert unsure.fields["invoice_number"].confidence < certain.fields[
        "invoice_number"
    ].confidence
    assert unsure.fields["invoice_number"].signals["model_uncertain"] is True


def test_an_invalid_gstin_scores_low() -> None:
    payload = {**BASE, "buyer": {"name": "B", "gstin": INVALID_GSTIN}}
    report = score(payload)
    assert report.fields["buyer.gstin"].band == "low"
    assert report.fields["buyer.gstin"].signals["format_valid"] is False


def test_a_valid_gstin_scores_higher_than_an_invalid_one() -> None:
    valid = score(BASE).fields["supplier.gstin"].confidence
    invalid = score(
        {**BASE, "supplier": {"name": "S", "gstin": INVALID_GSTIN}}
    ).fields["supplier.gstin"].confidence
    assert valid > invalid


def test_failing_arithmetic_drags_down_the_amounts_it_covers() -> None:
    good = score(BASE).fields["total"].confidence
    bad = score({**BASE, "total": 125000}).fields["total"].confidence
    assert bad < good
    assert score({**BASE, "total": 125000}).fields["total"].signals["cross_check"] == "failed"


def test_an_ambiguous_date_scores_lower_than_an_unambiguous_one() -> None:
    from app.pipelines.stages.parse import parse_provider_output

    ambiguous = parse_provider_output({**BASE, "invoice_date": "03/04/2026"})
    unambiguous = parse_provider_output({**BASE, "invoice_date": "18/09/2026"})

    scored_ambiguous = score_extraction(
        ambiguous.invoice,
        evidence={},
        document_text=None,
        ambiguous_fields=ambiguous.ambiguous_fields,
    )
    scored_plain = score_extraction(
        unambiguous.invoice, evidence={}, document_text=None
    )
    assert (
        scored_ambiguous.fields["invoice_date"].confidence
        < scored_plain.fields["invoice_date"].confidence
    )


def test_missing_text_layer_neither_helps_nor_hurts() -> None:
    """A scan has no text to check against; that is not evidence of a problem."""
    evidence = {"invoice_number": FieldEvidence("INV-29381", 1, True)}
    with_text = score(BASE, evidence, text=TEXT_LAYER)
    without_text = score(BASE, evidence, text=None)
    assert "found_in_text_layer" not in without_text.fields["invoice_number"].signals
    assert (
        without_text.fields["invoice_number"].confidence
        < with_text.fields["invoice_number"].confidence
    )


def test_null_fields_are_not_scored_at_all() -> None:
    report = score(BASE)
    # No value means no confidence entry — not a confident null.
    assert "due_date" not in report.fields
    assert "buyer.gstin" not in report.fields


def test_bands_follow_the_configured_thresholds() -> None:
    assert band_for(0.95) == "high"
    assert band_for(0.70) == "medium"
    assert band_for(0.20) == "low"


def test_the_overall_score_weights_the_fields_that_matter() -> None:
    strong = score(
        BASE,
        {
            "invoice_number": FieldEvidence("INV-29381", 1, True),
            "invoice_date": FieldEvidence("18/09/2026", 1, True),
            "supplier.gstin": FieldEvidence(SUPPLIER_GSTIN_KA, 1, True),
            "total": FieldEvidence("1,18,000.00", 1, True),
        },
    )
    weak = score({**BASE, "total": 125000})
    assert strong.overall > weak.overall
    assert strong.overall_band in {"high", "medium"}


def test_an_uncorroborated_field_is_medium_not_low() -> None:
    """LOW must mean "we doubt this", not "the prompt did not ask for a citation".

    Otherwise low_confidence_fields lists most of the document on a clean
    invoice and stops being a useful review queue.
    """
    report = score(BASE)
    assert report.fields["supplier.name"].band == "medium"
    assert report.fields["place_of_supply"].band in {"medium", "high"}


def test_low_confidence_is_reserved_for_fields_with_a_negative_signal() -> None:
    payload = {**BASE, "buyer": {"name": "B", "gstin": INVALID_GSTIN}, "total": 125000}
    report = score(payload)
    flagged = set(report.low_confidence_fields(0.60))
    assert "buyer.gstin" in flagged
    assert "total" in flagged
    # Fields with nothing wrong with them stay off the review queue.
    assert "supplier.name" not in flagged
    assert "invoice_number" not in flagged


def test_a_clean_invoice_has_a_short_review_queue() -> None:
    report = score(
        BASE,
        {
            "invoice_number": FieldEvidence("INV-29381", 1, True),
            "supplier.gstin": FieldEvidence(SUPPLIER_GSTIN_KA, 1, True),
            "total": FieldEvidence("1,18,000.00", 1, True),
        },
    )
    assert report.low_confidence_fields(0.60) == []
