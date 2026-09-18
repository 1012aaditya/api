"""Parsing and normalizing provider output (§7, §11, §13)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import ExtractionFailedError
from app.pipelines.stages.normalize import normalize_invoice
from app.pipelines.stages.parse import parse_provider_output
from app.utils.dates import parse_date
from tests.fixtures.invoices import SAMPLE_PROVIDER_OUTPUT


def test_a_well_formed_payload_round_trips() -> None:
    result = parse_provider_output(SAMPLE_PROVIDER_OUTPUT)
    invoice = result.invoice
    assert invoice.invoice_number == "INV-29381"
    assert invoice.invoice_date.isoformat() == "2026-09-18"
    assert invoice.total == Decimal("118000")
    assert len(invoice.items) == 2
    assert not result.dropped_fields


def test_indian_lakh_grouping_and_currency_symbols_are_parsed() -> None:
    result = parse_provider_output(
        {"subtotal": "₹1,00,000.00", "total": "Rs 1,18,000", "tax": {"cgst": "9,000.00"}}
    )
    assert result.invoice.subtotal == Decimal("100000.00")
    assert result.invoice.total == Decimal("118000")
    assert result.invoice.tax.cgst == Decimal("9000.00")


def test_placeholder_strings_become_null_not_text() -> None:
    result = parse_provider_output(
        {"invoice_number": "N/A", "place_of_supply": "  ", "payment_terms": "null"}
    )
    assert result.invoice.invoice_number is None
    assert result.invoice.place_of_supply is None
    assert result.invoice.payment_terms is None


def test_an_unparseable_amount_is_dropped_not_guessed() -> None:
    result = parse_provider_output({"total": "about twelve thousand"})
    assert result.invoice.total is None
    assert "total" in result.dropped_fields


def test_a_wrongly_typed_party_is_dropped_not_coerced() -> None:
    result = parse_provider_output({"buyer": "Marigold Retail Pvt Ltd"})
    assert result.invoice.buyer.name is None
    assert "buyer" in result.dropped_fields


def test_blank_line_item_rows_are_discarded() -> None:
    result = parse_provider_output(
        {"items": [{"description": "A", "quantity": 1}, {"description": None}, 42]}
    )
    assert len(result.invoice.items) == 1
    assert "items[2]" in result.dropped_fields


def test_ambiguous_dates_are_flagged() -> None:
    result = parse_provider_output({"invoice_date": "03/04/2026"})
    # Indian convention: DD/MM.
    assert result.invoice.invoice_date.isoformat() == "2026-04-03"
    assert "invoice_date" in result.ambiguous_fields


def test_unambiguous_dates_are_not_flagged() -> None:
    result = parse_provider_output({"invoice_date": "18/09/2026"})
    assert result.invoice.invoice_date.isoformat() == "2026-09-18"
    assert not result.ambiguous_fields


def test_an_impossible_date_becomes_null() -> None:
    result = parse_provider_output({"invoice_date": "31/02/2026"})
    assert result.invoice.invoice_date is None
    assert "invoice_date" in result.dropped_fields


@pytest.mark.parametrize("value", ["yes", "Yes", True])
def test_reverse_charge_truthy_forms(value: object) -> None:
    assert parse_provider_output({"reverse_charge": value}).invoice.reverse_charge is True


def test_reverse_charge_unknown_stays_null() -> None:
    assert parse_provider_output({"reverse_charge": "maybe"}).invoice.reverse_charge is None


def test_evidence_is_extracted_and_uncertainty_preserved() -> None:
    result = parse_provider_output(
        {
            "invoice_number": "INV-1",
            "_evidence": {
                "invoice_number": {"text": "INV-1", "page": 2, "certain": False}
            },
        }
    )
    evidence = result.evidence["invoice_number"]
    assert evidence.text == "INV-1"
    assert evidence.page == 2
    assert evidence.certain is False


def test_non_object_output_is_a_clear_failure() -> None:
    with pytest.raises(ExtractionFailedError):
        parse_provider_output(["not", "an", "object"])  # type: ignore[arg-type]


def test_booleans_are_not_silently_treated_as_amounts() -> None:
    result = parse_provider_output({"total": True})
    assert result.invoice.total is None
    assert "total" in result.dropped_fields


# --- normalization -----------------------------------------------------


def test_identifiers_are_uppercased_and_de_spaced() -> None:
    parsed = parse_provider_output(
        {
            "supplier": {"gstin": "29 aabcu 9603 r1zj", "pan": "aabcu9603r"},
            "items": [{"description": "X", "hsn_sac": "9983 14"}],
        }
    )
    invoice, report = normalize_invoice(parsed.invoice)
    assert invoice.supplier.gstin == "29AABCU9603R1ZJ"
    assert invoice.supplier.pan == "AABCU9603R"
    assert invoice.items[0].hsn_sac == "998314"
    assert "supplier.gstin" in report.changed_fields


@pytest.mark.parametrize(
    "raw,expected",
    [("₹", "INR"), ("Rs.", "INR"), ("inr", "INR"), ("usd", "USD"), ("gibberish", None)],
)
def test_currency_normalization(raw: str, expected: str | None) -> None:
    parsed = parse_provider_output({"currency": raw})
    invoice, _ = normalize_invoice(parsed.invoice)
    assert invoice.currency == expected


def test_irn_is_mirrored_to_the_top_level() -> None:
    parsed = parse_provider_output({"e_invoice_details": {"irn": "abc123"}})
    invoice, _ = normalize_invoice(parsed.invoice)
    assert invoice.irn == "abc123"
    assert invoice.e_invoice_details.irn == "abc123"


def test_normalization_never_invents_a_value() -> None:
    parsed = parse_provider_output({"invoice_number": "INV-1"})
    invoice, _ = normalize_invoice(parsed.invoice)
    assert invoice.total is None
    assert invoice.supplier.gstin is None
    assert invoice.tax.cgst is None
    assert invoice.items == []


@pytest.mark.parametrize(
    "text,expected,ambiguous",
    [
        ("2026-09-18", "2026-09-18", False),
        ("18/09/2026", "2026-09-18", False),
        ("18-Sep-2026", "2026-09-18", False),
        ("Sep 18, 2026", "2026-09-18", False),
        ("18.09.26", "2026-09-18", False),
        ("03/04/2026", "2026-04-03", True),
    ],
)
def test_date_formats_seen_on_indian_invoices(
    text: str, expected: str, ambiguous: bool
) -> None:
    result = parse_date(text)
    assert result.value.isoformat() == expected
    assert result.ambiguous is ambiguous
