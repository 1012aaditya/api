"""The business rules that run over every extraction (§9).

Each function takes the invoice and returns zero or more ``CheckResult``.
None of them mutate the invoice: validation reports, it never repairs. A rule
whose inputs are absent returns ``not_checked``, with a message saying which
input was missing, so an integrator can tell "we verified this" apart from
"we could not".
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.schemas.invoice import InvoiceData
from app.validators.base import (
    CheckResult,
    failed,
    not_checked,
    passed,
    warning,
)
from app.validators.gstin import (
    check_gstin,
    state_code_from_place_of_supply,
    state_name,
)

ZERO = Decimal("0")


def _n(value: Decimal | None) -> Decimal:
    return value if value is not None else ZERO


def _num(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _close(a: Decimal, b: Decimal, tolerance: Decimal) -> bool:
    return abs(a - b) <= tolerance


# --- Required fields (§9) ----------------------------------------------


def check_required_fields(invoice: InvoiceData, _tolerance: Decimal) -> list[CheckResult]:
    """Only the four fields an invoice is unusable without.

    Optional fields being absent is normal and must not fail the document.
    """
    missing: list[str] = []
    if not invoice.invoice_number:
        missing.append("invoice_number")
    if invoice.invoice_date is None:
        missing.append("invoice_date")
    if invoice.supplier.is_empty():
        missing.append("supplier")
    if invoice.total is None:
        missing.append("total")

    if not missing:
        return [passed("required_fields_present")]
    return [
        failed(
            "required_fields_present",
            f"Missing required field(s): {', '.join(missing)}.",
            missing=missing,
        )
    ]


# --- GSTIN (§9, §14) ---------------------------------------------------


def check_gstin_format(invoice: InvoiceData, _tolerance: Decimal) -> list[CheckResult]:
    results: list[CheckResult] = []
    for role, party in (("supplier", invoice.supplier), ("buyer", invoice.buyer)):
        name = f"{role}_gstin_format"
        result = check_gstin(party.gstin)
        if result is None:
            # A B2C invoice legitimately has no buyer GSTIN.
            results.append(
                not_checked(name, f"No {role} GSTIN was found on the document.")
            )
            continue
        if result.is_valid:
            results.append(
                passed(name, state=state_name(result.gstin), gstin=result.gstin)
            )
        else:
            results.append(
                failed(
                    name,
                    result.reason or "The GSTIN is not structurally valid.",
                    gstin=result.gstin,
                    structure_valid=result.structure_valid,
                    checksum_valid=result.checksum_valid,
                )
            )
    return results


def check_supply_type(invoice: InvoiceData, tolerance: Decimal) -> list[CheckResult]:
    """CGST+SGST for intrastate supply, IGST for interstate (§14).

    Requires both a supplier GSTIN and a resolvable place of supply. Without
    both, the jurisdiction is unknown and the check does not run.
    """
    name = "supply_type_consistency"
    supplier_state = (
        invoice.supplier.gstin[:2]
        if invoice.supplier.gstin and len(invoice.supplier.gstin) >= 2
        else None
    )
    supply_state = state_code_from_place_of_supply(invoice.place_of_supply)
    if supplier_state is None or supply_state is None:
        return [
            not_checked(
                name,
                "Needs both a supplier GSTIN and a resolvable place of supply.",
                supplier_state=supplier_state,
                place_of_supply_state=supply_state,
            )
        ]

    intrastate = supplier_state == supply_state
    cgst_sgst = _n(invoice.tax.cgst) + _n(invoice.tax.sgst) + _n(invoice.tax.utgst)
    igst = _n(invoice.tax.igst)

    if cgst_sgst <= tolerance and igst <= tolerance:
        return [
            not_checked(
                name,
                "No GST amounts were found, so the supply type cannot be checked.",
            )
        ]

    if intrastate and igst > tolerance:
        return [
            failed(
                name,
                "Supplier and place of supply are in the same state, which attracts "
                "CGST and SGST, but IGST was charged.",
                supplier_state=supplier_state,
                place_of_supply_state=supply_state,
                igst=_num(invoice.tax.igst),
            )
        ]
    if not intrastate and cgst_sgst > tolerance:
        return [
            failed(
                name,
                "Supplier and place of supply are in different states, which attracts "
                "IGST, but CGST/SGST was charged.",
                supplier_state=supplier_state,
                place_of_supply_state=supply_state,
                cgst=_num(invoice.tax.cgst),
                sgst=_num(invoice.tax.sgst),
            )
        ]
    return [
        passed(
            name,
            "Intrastate supply (CGST + SGST)."
            if intrastate
            else "Interstate supply (IGST).",
            supply_type="intrastate" if intrastate else "interstate",
        )
    ]


def check_cgst_sgst_split(invoice: InvoiceData, tolerance: Decimal) -> list[CheckResult]:
    """CGST and SGST are levied at equal rates, so the amounts must match."""
    name = "cgst_sgst_split"
    cgst, sgst = invoice.tax.cgst, invoice.tax.sgst
    if cgst is None and sgst is None:
        return [not_checked(name, "The invoice does not report CGST or SGST.")]
    if cgst is None or sgst is None:
        present, absent = ("CGST", "SGST") if cgst is not None else ("SGST", "CGST")
        return [
            warning(
                name,
                f"{present} is present but {absent} is not. They are normally levied together.",
                cgst=_num(cgst),
                sgst=_num(sgst),
            )
        ]
    if _close(cgst, sgst, tolerance):
        return [passed(name)]
    return [
        failed(
            name,
            "CGST and SGST differ; they are levied at equal rates.",
            cgst=_num(cgst),
            sgst=_num(sgst),
            difference=float(abs(cgst - sgst)),
        )
    ]


# --- Arithmetic (§9) ---------------------------------------------------


def check_invoice_total(invoice: InvoiceData, tolerance: Decimal) -> list[CheckResult]:
    """taxable base + taxes + charges + round-off ≈ printed total."""
    name = "invoice_total"
    if invoice.total is None:
        return [not_checked(name, "The invoice total was not extracted.")]

    taxable = invoice.tax.taxable_amount
    if taxable is not None:
        # A printed taxable amount is already net of discount.
        base = taxable
        base_source = "tax.taxable_amount"
    elif invoice.subtotal is not None:
        base = invoice.subtotal - _n(invoice.discount)
        base_source = "subtotal - discount"
    else:
        return [
            not_checked(
                name,
                "Neither a taxable amount nor a subtotal was extracted, so the "
                "total cannot be reconciled.",
            )
        ]

    taxes = (
        _n(invoice.tax.cgst)
        + _n(invoice.tax.sgst)
        + _n(invoice.tax.igst)
        + _n(invoice.tax.utgst)
        + _n(invoice.tax.cess)
    )
    computed = base + taxes + _n(invoice.other_charges) + _n(invoice.round_off)
    difference = invoice.total - computed

    details: dict[str, Any] = {
        "expected_total": float(computed),
        "reported_total": float(invoice.total),
        "difference": float(difference),
        "base_source": base_source,
        "tolerance": float(tolerance),
    }
    if _close(computed, invoice.total, tolerance):
        return [passed(name, **details)]
    return [
        failed(
            name,
            f"Components sum to {computed}, but the invoice reports {invoice.total}.",
            **details,
        )
    ]


def check_taxable_amount(invoice: InvoiceData, tolerance: Decimal) -> list[CheckResult]:
    """subtotal − discount ≈ taxable amount, when all three are present."""
    name = "taxable_amount_consistency"
    if invoice.tax.taxable_amount is None or invoice.subtotal is None:
        return [
            not_checked(
                name, "Needs both a subtotal and a taxable amount to compare."
            )
        ]
    expected = invoice.subtotal - _n(invoice.discount)
    if _close(expected, invoice.tax.taxable_amount, tolerance):
        return [passed(name)]
    return [
        warning(
            name,
            f"Subtotal minus discount is {expected}, but the taxable amount is "
            f"{invoice.tax.taxable_amount}.",
            subtotal=_num(invoice.subtotal),
            discount=_num(invoice.discount),
            taxable_amount=_num(invoice.tax.taxable_amount),
        )
    ]


def check_line_items(invoice: InvoiceData, tolerance: Decimal) -> list[CheckResult]:
    """quantity × unit price − discount ≈ taxable value, per row."""
    name = "line_item_calculation"
    if not invoice.items:
        return [not_checked(name, "The invoice has no line items.")]

    checkable = 0
    mismatches: list[dict[str, Any]] = []
    for index, item in enumerate(invoice.items):
        if item.quantity is None or item.unit_price is None or item.taxable_value is None:
            continue
        checkable += 1
        expected = item.quantity * item.unit_price - _n(item.discount)
        if not _close(expected, item.taxable_value, tolerance):
            mismatches.append(
                {
                    "index": index,
                    "description": item.description,
                    "expected_taxable_value": float(expected),
                    "reported_taxable_value": float(item.taxable_value),
                }
            )

    if checkable == 0:
        return [
            not_checked(
                name,
                "No line item had quantity, unit price and taxable value together.",
                item_count=len(invoice.items),
            )
        ]
    if not mismatches:
        return [passed(name, items_checked=checkable, item_count=len(invoice.items))]

    # A minority of bad rows is a warning; the row-level detail is in details.
    status_fn = failed if len(mismatches) == checkable else warning
    return [
        status_fn(
            name,
            f"{len(mismatches)} of {checkable} checkable line items did not reconcile.",
            items_checked=checkable,
            mismatches=mismatches[:10],
        )
    ]


def check_line_item_sum(invoice: InvoiceData, tolerance: Decimal) -> list[CheckResult]:
    """Sum of line taxable values ≈ the invoice-level taxable amount."""
    name = "line_item_sum"
    target = invoice.tax.taxable_amount or invoice.subtotal
    if target is None:
        return [not_checked(name, "No invoice-level taxable amount or subtotal.")]
    values = [item.taxable_value for item in invoice.items if item.taxable_value is not None]
    if not values or len(values) != len(invoice.items):
        return [
            not_checked(
                name,
                "Not every line item has a taxable value, so they cannot be summed.",
                items_with_value=len(values),
                item_count=len(invoice.items),
            )
        ]
    total = sum(values, ZERO)
    # Line items are pre-discount; allow the invoice discount to explain a gap.
    if _close(total, target, tolerance) or _close(
        total - _n(invoice.discount), target, tolerance
    ):
        return [passed(name, line_item_total=float(total), target=float(target))]
    return [
        warning(
            name,
            f"Line items sum to {total}, but the invoice reports {target}.",
            line_item_total=float(total),
            target=float(target),
            difference=float(target - total),
        )
    ]


ALL_CHECKS = (
    check_required_fields,
    check_gstin_format,
    check_supply_type,
    check_cgst_sgst_split,
    check_invoice_total,
    check_taxable_amount,
    check_line_items,
    check_line_item_sum,
)


# --- Cross-source agreement (§11) --------------------------------------
#
# Not part of ALL_CHECKS: it needs the merge result, not just the invoice.
# The pipeline appends it when more than one tier contributed.


def check_source_agreement(conflicts: list[dict[str, Any]]) -> CheckResult:
    """Did the independent readings of this document agree?

    When the e-invoice QR, the PDF's text layer and the model all read the
    same field, that agreement is real evidence. When they disagree, the
    caller needs to know which value they are getting and what the other
    source said — not a quietly chosen winner.
    """
    name = "source_agreement"
    if not conflicts:
        return passed(name, "Every source that read a field agreed on it.")
    fields = ", ".join(sorted({str(c["field"]) for c in conflicts}))
    return warning(
        name,
        f"Sources disagreed on {fields}. The more direct source was used; the "
        "alternative reading is in the details.",
        conflicts=conflicts[:10],
    )
