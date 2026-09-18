"""Normalization (§11).

Reshaping a printed value into a canonical form is not the same as inventing
one, and this stage only does the former: case and spacing on identifiers
that have a defined character set, a currency code, and mirroring the
e-invoice IRN to the top-level field the schema also exposes.

It never fills a null, never computes a missing total, and never corrects a
value it thinks looks wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.schemas.invoice import InvoiceData, Party

_IDENTIFIER_NOISE = re.compile(r"[\s\-]")

_CURRENCY_ALIASES = {
    "inr": "INR", "rs": "INR", "rs.": "INR", "rupees": "INR", "rupee": "INR",
    "₹": "INR", "inr.": "INR", "indian rupee": "INR", "indian rupees": "INR",
}
_CURRENCY_CODE = re.compile(r"^[A-Za-z]{3}$")


@dataclass
class NormalizationReport:
    """What normalization changed, so the caller can reason about it."""

    changed_fields: list[str] = field(default_factory=list)


def _normalize_identifier(value: str | None) -> str | None:
    """Uppercase and de-space a GSTIN / PAN / HSN, which are defined uppercase."""
    if value is None:
        return None
    cleaned = _IDENTIFIER_NOISE.sub("", value).upper()
    return cleaned or None


def _normalize_email(value: str | None) -> str | None:
    return value.lower() if value else None


def _normalize_currency(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.strip().lower()
    if lowered in _CURRENCY_ALIASES:
        return _CURRENCY_ALIASES[lowered]
    if _CURRENCY_CODE.match(lowered):
        return lowered.upper()
    if "₹" in value or "rs" in lowered:
        return "INR"
    return None


def _normalize_party(party: Party, prefix: str, report: NormalizationReport) -> Party:
    gstin = _normalize_identifier(party.gstin)
    pan = _normalize_identifier(party.pan)
    email = _normalize_email(party.email)
    if gstin != party.gstin:
        report.changed_fields.append(f"{prefix}.gstin")
    if pan != party.pan:
        report.changed_fields.append(f"{prefix}.pan")
    return party.model_copy(update={"gstin": gstin, "pan": pan, "email": email})


def normalize_invoice(invoice: InvoiceData) -> tuple[InvoiceData, NormalizationReport]:
    report = NormalizationReport()
    updates: dict[str, object] = {}

    updates["supplier"] = _normalize_party(invoice.supplier, "supplier", report)
    updates["buyer"] = _normalize_party(invoice.buyer, "buyer", report)

    currency = _normalize_currency(invoice.currency)
    if currency != invoice.currency:
        report.changed_fields.append("currency")
    updates["currency"] = currency

    items = []
    for index, item in enumerate(invoice.items):
        hsn = _normalize_identifier(item.hsn_sac)
        if hsn != item.hsn_sac:
            report.changed_fields.append(f"items[{index}].hsn_sac")
        items.append(item.model_copy(update={"hsn_sac": hsn}))
    updates["items"] = items

    if invoice.bank_details is not None:
        ifsc = _normalize_identifier(invoice.bank_details.ifsc)
        if ifsc != invoice.bank_details.ifsc:
            report.changed_fields.append("bank_details.ifsc")
        updates["bank_details"] = invoice.bank_details.model_copy(update={"ifsc": ifsc})

    # §7 exposes irn/qr_code_data both nested and at the top level. Keep the
    # two in step by copying whichever side was populated — a copy, not a guess.
    einv = invoice.e_invoice_details
    irn = invoice.irn or (einv.irn if einv else None)
    qr = invoice.qr_code_data or (einv.qr_code_data if einv else None)
    if irn != invoice.irn:
        report.changed_fields.append("irn")
    if qr != invoice.qr_code_data:
        report.changed_fields.append("qr_code_data")
    updates["irn"] = irn
    updates["qr_code_data"] = qr
    if einv is not None:
        updates["e_invoice_details"] = einv.model_copy(
            update={"irn": einv.irn or irn, "qr_code_data": einv.qr_code_data or qr}
        )

    return invoice.model_copy(update=updates), report
