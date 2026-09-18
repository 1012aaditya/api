"""Tier 1 — read labelled fields straight out of a digital PDF's text layer.

Most B2B invoices are generated PDFs, not scans: the characters are already
in the file. For those, a vision model is an expensive way to read text that
can simply be read.

This tier is deliberately conservative. It extracts a value only when a label
it recognises sits next to it, and it leaves everything else null for a later
tier. It does not attempt line-item tables — column reconstruction without
layout analysis guesses, and a guessed line item is worse than none.

Nothing here infers a party's identity without evidence: a GSTIN is assigned
to the supplier or the buyer only when a role label says so, or (flagged as
uncertain) when the document carries exactly one and it sits in the header.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.core.logging import get_logger
from app.pipelines.stages.parse import FieldEvidence
from app.pipelines.stages.preprocess import PreparedDocument
from app.pipelines.tiers import ExtractionTier, TierOutput
from app.schemas.invoice import InvoiceData, Party, TaxBreakdown
from app.utils.dates import parse_date
from app.validators.gstin import GSTIN_PATTERN, is_valid_gstin

logger = get_logger("docuparse.text_layer")

_GSTIN_ANYWHERE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")
# An Indian-formatted amount: optional symbol, lakh grouping, optional decimals.
_AMOUNT = re.compile(r"(?:₹|Rs\.?|INR)?\s*(-?\d{1,3}(?:[,\d]{0,12})(?:\.\d{1,2})?)(?!\s*%)")
_LABEL_SPLIT = re.compile(r"[:\-–—]\s*")

SUPPLIER_MARKERS = (
    "supplier", "seller", "sold by", "from", "billed by", "service provider",
    "dispatch from", "issued by",
)
BUYER_MARKERS = (
    "bill to", "billed to", "buyer", "recipient", "customer", "consignee",
    "ship to", "shipped to", "sold to", "party",
)

# Ordered most-specific first: "Total" must not win over "Total Taxable Value".
TOTAL_LABELS = (
    "total invoice value", "grand total", "invoice total", "total amount payable",
    "amount payable", "net payable", "net amount", "total amount", "invoice value",
    "grand amount", "total",
)
TAXABLE_LABELS = (
    "total taxable value", "total taxable amount", "taxable value", "taxable amount",
)
SUBTOTAL_LABELS = ("sub total", "subtotal", "total before tax", "gross amount", "gross total")
ROUND_OFF_LABELS = ("round off", "rounding off", "rounded off", "round-off")
DISCOUNT_LABELS = ("discount", "less discount", "total discount")
OTHER_CHARGES_LABELS = ("other charges", "freight", "packing charges", "shipping charges")

INVOICE_NUMBER_LABELS = (
    "tax invoice no", "invoice no", "invoice number", "invoice #", "bill no",
    "bill number", "document no", "doc no", "inv no", "invoice",
)
INVOICE_DATE_LABELS = (
    "invoice date", "tax invoice date", "bill date", "document date", "doc date",
    "dated", "date of issue", "date",
)
DUE_DATE_LABELS = ("due date", "payment due", "due on")
PLACE_OF_SUPPLY_LABELS = ("place of supply", "pos", "state of supply")
REVERSE_CHARGE_LABELS = ("reverse charge", "whether tax is payable on reverse charge")

TAX_LABELS: dict[str, tuple[str, ...]] = {
    "cgst": ("cgst",),
    "sgst": ("sgst",),
    "utgst": ("utgst",),
    "igst": ("igst",),
    "cess": ("cess",),
}


@dataclass
class _Line:
    index: int
    text: str

    @property
    def lowered(self) -> str:
        return self.text.lower()


def _lines(text: str) -> list[_Line]:
    return [
        _Line(index, cleaned)
        for index, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
        if (cleaned := " ".join(raw.split()))
    ]


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _amount_on(line: str) -> Decimal | None:
    """The last money-looking number on the line, ignoring percentages."""
    matches = _AMOUNT.findall(line)
    for candidate in reversed(matches):
        value = _to_decimal(candidate)
        # A bare "9" from "CGST @ 9%" is filtered by the regex's lookahead, but
        # a lone small integer is far more likely a rate or a count than money.
        if value is not None:
            return value
    return None


def _find_labelled_amount(
    lines: list[_Line], labels: tuple[str, ...]
) -> tuple[Decimal, _Line, str] | None:
    for label in labels:
        for line in lines:
            position = line.lowered.find(label)
            if position == -1:
                continue
            # The label must start the line or follow a separator, so "Total"
            # does not match inside "Subtotal".
            before = line.lowered[:position].strip()
            if before and not before.endswith((":", "-", "|")) and len(before) > 2:
                continue
            remainder = line.text[position + len(label) :]
            amount = _amount_on(remainder) if remainder.strip() else _amount_on(line.text)
            if amount is not None:
                return amount, line, label
    return None


def _find_labelled_text(
    lines: list[_Line], labels: tuple[str, ...], *, max_length: int = 120
) -> tuple[str, _Line, str] | None:
    for label in labels:
        for line in lines:
            position = line.lowered.find(label)
            if position == -1:
                continue
            before = line.lowered[:position].strip()
            if before and len(before) > 2:
                continue
            tail = line.text[position + len(label) :]
            value = _LABEL_SPLIT.sub("", tail, count=1).strip() if tail else ""
            value = value.strip(" :-–—|")
            # Some layouts put the label on one line and the value on the next.
            if not value and line.index + 1 < len(lines):
                following = next((n for n in lines if n.index == line.index + 1), None)
                if following and len(following.text) <= max_length:
                    value = following.text
            if value:
                return value[:max_length].strip(), line, label
    return None


def _nearest_role(lines: list[_Line], target_index: int) -> str | None:
    """Which party the nearest preceding role label names, if any."""
    best: tuple[int, str] | None = None
    for line in lines:
        if line.index > target_index:
            continue
        distance = target_index - line.index
        if distance > 6:
            continue
        for marker in BUYER_MARKERS:
            if marker in line.lowered:
                if best is None or distance < best[0]:
                    best = (distance, "buyer")
        for marker in SUPPLIER_MARKERS:
            if marker in line.lowered:
                if best is None or distance < best[0]:
                    best = (distance, "supplier")
    return best[1] if best else None


def extract_from_text_layer(document: PreparedDocument) -> TierOutput | None:
    """Read what the text layer states plainly. Returns None if there is none."""
    if not document.has_text_layer:
        return None

    lines = _lines(document.embedded_text or "")
    if len(lines) < 3:
        return None

    populated: set[str] = set()
    evidence: dict[str, FieldEvidence] = {}
    notes: list[str] = []
    values: dict[str, object] = {"currency": None}

    def record(path: str, printed: str, *, certain: bool = True) -> None:
        populated.add(path)
        evidence[path] = FieldEvidence(text=printed, page=1, certain=certain)

    # --- identity ---
    if found := _find_labelled_text(lines, INVOICE_NUMBER_LABELS, max_length=60):
        number, line, _ = found
        values["invoice_number"] = number
        record("invoice_number", number)
        _ = line

    if found := _find_labelled_text(lines, INVOICE_DATE_LABELS, max_length=40):
        printed, _, _ = found
        parsed = parse_date(printed)
        if parsed.value is not None:
            values["invoice_date"] = parsed.value
            record("invoice_date", printed, certain=not parsed.ambiguous)

    if found := _find_labelled_text(lines, DUE_DATE_LABELS, max_length=40):
        printed, _, _ = found
        parsed = parse_date(printed)
        if parsed.value is not None:
            values["due_date"] = parsed.value
            populated.add("due_date")

    if found := _find_labelled_text(lines, PLACE_OF_SUPPLY_LABELS, max_length=60):
        printed, _, _ = found
        values["place_of_supply"] = printed
        record("place_of_supply", printed)

    if found := _find_labelled_text(lines, REVERSE_CHARGE_LABELS, max_length=20):
        printed, _, _ = found
        lowered = printed.lower()
        if lowered.startswith(("y", "n")):
            values["reverse_charge"] = lowered.startswith("y")
            populated.add("reverse_charge")

    # --- parties ---
    supplier_gstin, buyer_gstin = _assign_gstins(lines, notes)
    if supplier_gstin:
        gstin, certain = supplier_gstin
        values["supplier"] = Party(gstin=gstin)
        record("supplier.gstin", gstin, certain=certain)
    if buyer_gstin:
        gstin, certain = buyer_gstin
        values["buyer"] = Party(gstin=gstin)
        record("buyer.gstin", gstin, certain=certain)

    # --- money ---
    tax_values: dict[str, Decimal] = {}
    for field_name, labels in TAX_LABELS.items():
        if found := _find_labelled_amount(lines, labels):
            amount, line, _ = found
            tax_values[field_name] = amount
            record(f"tax.{field_name}", line.text)

    if found := _find_labelled_amount(lines, TAXABLE_LABELS):
        amount, line, _ = found
        tax_values["taxable_amount"] = amount
        record("tax.taxable_amount", line.text)
    values["tax"] = TaxBreakdown(**tax_values) if tax_values else TaxBreakdown()

    for field_name, labels in (
        ("total", TOTAL_LABELS),
        ("subtotal", SUBTOTAL_LABELS),
        ("round_off", ROUND_OFF_LABELS),
        ("discount", DISCOUNT_LABELS),
        ("other_charges", OTHER_CHARGES_LABELS),
    ):
        if found := _find_labelled_amount(lines, labels):
            amount, line, _ = found
            values[field_name] = amount
            record(field_name, line.text)

    if any("₹" in line.text or "rs" in line.lowered or "inr" in line.lowered for line in lines):
        values["currency"] = "INR"
        populated.add("currency")

    if not populated:
        return None

    notes.append(
        "Read from the PDF's own text layer. Line items are not extracted at "
        "this tier — reconstructing table columns without layout analysis "
        "guesses, and a guessed line item is worse than none."
    )
    logger.info("text_layer.extracted", fields=len(populated), lines=len(lines))
    return TierOutput(
        tier=ExtractionTier.TEXT_LAYER,
        invoice=InvoiceData.model_validate(values),
        evidence=evidence,
        populated_paths=populated,
        notes=notes,
    )


def _assign_gstins(
    lines: list[_Line], notes: list[str]
) -> tuple[tuple[str, bool] | None, tuple[str, bool] | None]:
    """Map the GSTINs on the page to supplier and buyer, or to neither."""
    found: list[tuple[str, int]] = []
    for line in lines:
        for match in _GSTIN_ANYWHERE.finditer(line.text.upper()):
            candidate = match.group(0)
            if GSTIN_PATTERN.match(candidate):
                found.append((candidate, line.index))

    if not found:
        return None, None

    supplier: tuple[str, bool] | None = None
    buyer: tuple[str, bool] | None = None
    unassigned: list[tuple[str, int]] = []

    for gstin, index in found:
        role = _nearest_role(lines, index)
        if role == "supplier" and supplier is None:
            supplier = (gstin, True)
        elif role == "buyer" and buyer is None:
            buyer = (gstin, True)
        else:
            unassigned.append((gstin, index))

    if supplier is None and buyer is None and len(found) == 1:
        gstin, index = found[0]
        # A GST invoice's issuer is always registered; its buyer may not be. A
        # lone GSTIN in the header is therefore very likely the supplier's —
        # but "very likely" is not "stated", so it is flagged uncertain and
        # scored down rather than presented as read.
        if index <= max(4, len(lines) // 3):
            supplier = (gstin, False)
            notes.append(
                "One GSTIN was found with no supplier or buyer label beside it. "
                "It was attributed to the supplier because it appears in the "
                "header, and marked uncertain."
            )
        else:
            notes.append(
                "A GSTIN was found but no role label identified whose it is, so "
                "it was left unassigned."
            )
    elif unassigned:
        notes.append(
            f"{len(unassigned)} further GSTIN(s) were found without a role label "
            "and left unassigned."
        )

    for role_value in (supplier, buyer):
        if role_value and not is_valid_gstin(role_value[0]):
            notes.append(f"GSTIN {role_value[0]} failed its checksum.")
    return supplier, buyer
