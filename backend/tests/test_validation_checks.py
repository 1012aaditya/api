"""The business validation engine (§9, §10)."""

from __future__ import annotations

from decimal import Decimal

from app.schemas.invoice import InvoiceData
from app.validators.base import CheckStatus
from app.validators.engine import validate_invoice
from tests.fixtures.invoices import (
    INVALID_GSTIN,
    SUPPLIER_GSTIN_KA,
    SUPPLIER_GSTIN_MH,
)

TOLERANCE = Decimal("1.0")


def run(payload: dict) -> dict[str, tuple[CheckStatus, str | None]]:
    outcome = validate_invoice(
        InvoiceData.model_validate(payload), rounding_tolerance=TOLERANCE
    )
    return {c.name: (c.status, c.message) for c in outcome.checks}


CLEAN_INTRASTATE = {
    "invoice_number": "INV-1",
    "invoice_date": "2026-09-18",
    "place_of_supply": "29-Karnataka",
    "supplier": {"name": "S", "gstin": SUPPLIER_GSTIN_KA},
    "buyer": {"name": "B"},
    "items": [
        {"description": "X", "quantity": 2, "unit_price": 50000, "taxable_value": 100000}
    ],
    "subtotal": 100000,
    "total": 118000,
    "tax": {"taxable_amount": 100000, "cgst": 9000, "sgst": 9000},
}


def test_a_clean_invoice_passes_everything_that_runs() -> None:
    outcome = validate_invoice(
        InvoiceData.model_validate(CLEAN_INTRASTATE), rounding_tolerance=TOLERANCE
    )
    assert outcome.overall is CheckStatus.PASSED
    assert not outcome.failed_names


def test_totals_that_do_not_reconcile_fail_with_the_arithmetic() -> None:
    checks = run({**CLEAN_INTRASTATE, "total": 125000})
    status, message = checks["invoice_total"]
    assert status is CheckStatus.FAILED
    assert "118000" in message and "125000" in message


def test_rounding_within_tolerance_still_passes() -> None:
    assert run({**CLEAN_INTRASTATE, "total": 118000.75})["invoice_total"][0] is (
        CheckStatus.PASSED
    )


def test_round_off_line_is_taken_into_account() -> None:
    payload = {**CLEAN_INTRASTATE, "total": 118002, "round_off": 2}
    assert run(payload)["invoice_total"][0] is CheckStatus.PASSED


def test_missing_total_is_not_checked_rather_than_failed() -> None:
    checks = run({**CLEAN_INTRASTATE, "total": None})
    assert checks["invoice_total"][0] is CheckStatus.NOT_CHECKED


def test_required_fields_failure_names_what_is_missing() -> None:
    checks = run({**CLEAN_INTRASTATE, "invoice_number": None, "invoice_date": None})
    status, message = checks["required_fields_present"]
    assert status is CheckStatus.FAILED
    assert "invoice_number" in message and "invoice_date" in message


def test_missing_optional_fields_do_not_fail_the_document() -> None:
    payload = {
        k: v
        for k, v in CLEAN_INTRASTATE.items()
        if k not in {"items", "subtotal"}
    }
    outcome = validate_invoice(
        InvoiceData.model_validate(payload), rounding_tolerance=TOLERANCE
    )
    assert outcome.overall is CheckStatus.PASSED


def test_missing_buyer_gstin_is_not_checked() -> None:
    assert run(CLEAN_INTRASTATE)["buyer_gstin_format"][0] is CheckStatus.NOT_CHECKED


def test_invalid_gstin_fails() -> None:
    payload = {**CLEAN_INTRASTATE, "buyer": {"name": "B", "gstin": INVALID_GSTIN}}
    assert run(payload)["buyer_gstin_format"][0] is CheckStatus.FAILED


def test_igst_on_an_intrastate_supply_fails() -> None:
    payload = {
        **CLEAN_INTRASTATE,
        "tax": {"taxable_amount": 100000, "igst": 18000},
    }
    status, message = run(payload)["supply_type_consistency"]
    assert status is CheckStatus.FAILED
    assert "same state" in message


def test_cgst_sgst_on_an_interstate_supply_fails() -> None:
    payload = {**CLEAN_INTRASTATE, "supplier": {"name": "S", "gstin": SUPPLIER_GSTIN_MH}}
    status, message = run(payload)["supply_type_consistency"]
    assert status is CheckStatus.FAILED
    assert "different states" in message


def test_correct_interstate_invoice_passes() -> None:
    payload = {
        **CLEAN_INTRASTATE,
        "supplier": {"name": "S", "gstin": SUPPLIER_GSTIN_MH},
        "tax": {"taxable_amount": 100000, "igst": 18000},
    }
    assert run(payload)["supply_type_consistency"][0] is CheckStatus.PASSED


def test_supply_type_is_not_checked_without_a_resolvable_place_of_supply() -> None:
    payload = {**CLEAN_INTRASTATE, "place_of_supply": "Somewhere"}
    assert run(payload)["supply_type_consistency"][0] is CheckStatus.NOT_CHECKED


def test_unequal_cgst_and_sgst_fails() -> None:
    payload = {**CLEAN_INTRASTATE, "tax": {"taxable_amount": 100000, "cgst": 9000, "sgst": 8000}}
    assert run(payload)["cgst_sgst_split"][0] is CheckStatus.FAILED


def test_cgst_without_sgst_is_a_warning_not_a_failure() -> None:
    payload = {**CLEAN_INTRASTATE, "tax": {"taxable_amount": 100000, "cgst": 9000}}
    assert run(payload)["cgst_sgst_split"][0] is CheckStatus.WARNING


def test_line_item_arithmetic_is_checked_per_row() -> None:
    payload = {
        **CLEAN_INTRASTATE,
        "items": [
            {"description": "A", "quantity": 2, "unit_price": 50000, "taxable_value": 100000},
            {"description": "B", "quantity": 3, "unit_price": 100, "taxable_value": 999},
        ],
    }
    status, message = run(payload)["line_item_calculation"]
    assert status is CheckStatus.WARNING
    assert "1 of 2" in message


def test_all_line_items_wrong_is_a_failure() -> None:
    payload = {
        **CLEAN_INTRASTATE,
        "items": [{"description": "B", "quantity": 3, "unit_price": 100, "taxable_value": 999}],
    }
    assert run(payload)["line_item_calculation"][0] is CheckStatus.FAILED


def test_line_item_discount_is_honoured() -> None:
    payload = {
        **CLEAN_INTRASTATE,
        "items": [
            {
                "description": "A", "quantity": 2, "unit_price": 50000,
                "discount": 5000, "taxable_value": 95000,
            }
        ],
    }
    assert run(payload)["line_item_calculation"][0] is CheckStatus.PASSED


def test_line_items_without_enough_data_are_not_checked() -> None:
    payload = {**CLEAN_INTRASTATE, "items": [{"description": "A", "taxable_value": 100000}]}
    assert run(payload)["line_item_calculation"][0] is CheckStatus.NOT_CHECKED


def test_taxable_amount_reconciles_against_subtotal_and_discount() -> None:
    payload = {
        **CLEAN_INTRASTATE,
        "subtotal": 110000,
        "discount": 10000,
        "tax": {"taxable_amount": 100000, "cgst": 9000, "sgst": 9000},
    }
    assert run(payload)["taxable_amount_consistency"][0] is CheckStatus.PASSED


def test_taxable_amount_mismatch_is_a_warning() -> None:
    payload = {**CLEAN_INTRASTATE, "subtotal": 90000}
    assert run(payload)["taxable_amount_consistency"][0] is CheckStatus.WARNING


def test_an_empty_invoice_is_not_reported_as_passed() -> None:
    """Nothing verified must never look the same as everything verified."""
    outcome = validate_invoice(InvoiceData(), rounding_tolerance=TOLERANCE)
    assert outcome.overall is CheckStatus.FAILED  # required fields are missing
    statuses = {c.status for c in outcome.checks}
    assert CheckStatus.PASSED not in statuses


def test_a_failing_check_cannot_crash_the_engine(monkeypatch) -> None:
    import app.validators.checks as checks_module
    import app.validators.engine as engine_module

    def exploding(_invoice, _tolerance):
        raise RuntimeError("boom")

    exploding.__name__ = "check_exploding"
    monkeypatch.setattr(
        engine_module, "ALL_CHECKS", (*checks_module.ALL_CHECKS, exploding)
    )
    outcome = validate_invoice(
        InvoiceData.model_validate(CLEAN_INTRASTATE), rounding_tolerance=TOLERANCE
    )
    broken = outcome.by_name("exploding")
    assert broken is not None
    # Reported as unrun — never silently counted as a pass.
    assert broken.status is CheckStatus.NOT_CHECKED
