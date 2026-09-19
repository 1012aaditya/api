"""Synthetic invoice fixtures.

Everything here is invented. No real customer document, GSTIN or trading name
appears in this repository (§32). The GSTINs below are checksum-valid so the
validator exercises its real path, but they are constructed, not looked up,
and do not identify any registered taxpayer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from io import BytesIO
from typing import Any

from tests.fixtures.pdf_builder import PDFBuilder

# Checksum-valid, entirely fictional.
SUPPLIER_GSTIN_KA = "29AABCU9603R1ZJ"
BUYER_GSTIN_KA = "29AABCU9603R1ZJ"
BUYER_GSTIN_TN = "33AAACT2727Q1Z3"
SUPPLIER_GSTIN_MH = "27AAPFU0939F1ZV"
INVALID_GSTIN = "29ABCDE1234F1Z5"  # correct shape, wrong check digit


# --- What the provider is expected to return ---------------------------

SAMPLE_PROVIDER_OUTPUT: dict[str, Any] = {
    "invoice_number": "INV-29381",
    "invoice_date": "2026-09-18",
    "due_date": "2026-10-18",
    "document_subtype": "invoice",
    "place_of_supply": "29-Karnataka",
    "reverse_charge": False,
    "supplier": {
        "name": "Udupi Software Systems Pvt Ltd",
        "gstin": SUPPLIER_GSTIN_KA,
        "address": "4th Floor, Nandi Towers, Bengaluru, Karnataka 560001",
        "phone": "+91 80 4000 1234",
        "email": "billing@udupisoftware.example",
    },
    "buyer": {
        "name": "Marigold Retail Pvt Ltd",
        "gstin": BUYER_GSTIN_KA,
        "address": "Plot 19, Whitefield, Bengaluru, Karnataka 560066",
    },
    "billing_address": "Plot 19, Whitefield, Bengaluru, Karnataka 560066",
    "shipping_address": None,
    "items": [
        {
            "description": "Annual platform licence",
            "hsn_sac": "998314",
            "quantity": 1,
            "unit": "NOS",
            "unit_price": 80000,
            "taxable_value": 80000,
            "tax_rate": 18,
            "cgst": 7200,
            "sgst": 7200,
            "total": 94400,
        },
        {
            "description": "Onboarding and configuration",
            "hsn_sac": "998313",
            "quantity": 20,
            "unit": "HRS",
            "unit_price": 1000,
            "taxable_value": 20000,
            "tax_rate": 18,
            "cgst": 1800,
            "sgst": 1800,
            "total": 23600,
        },
    ],
    "subtotal": 100000,
    "discount": None,
    "other_charges": None,
    "round_off": None,
    "total": 118000,
    "currency": "INR",
    "tax": {
        "taxable_amount": 100000,
        "cgst": 9000,
        "sgst": 9000,
        "igst": None,
        "utgst": None,
        "cess": None,
    },
    "payment_terms": "Net 30",
    "bank_details": {
        "account_name": "Udupi Software Systems Pvt Ltd",
        "account_number": "004405001234",
        "ifsc": "HDFC0000445",
        "bank_name": "HDFC Bank",
        "branch": "Bengaluru MG Road",
    },
    "e_invoice_details": {
        "irn": "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90",
        "ack_number": "112410012345678",
        "ack_date": "2026-09-18",
        "qr_code_data": None,
    },
    "_evidence": {
        "invoice_number": {"text": "INV-29381", "page": 1, "certain": True},
        "invoice_date": {"text": "18/09/2026", "page": 1, "certain": True},
        "supplier.name": {"text": "Udupi Software Systems Pvt Ltd", "page": 1, "certain": True},
        "supplier.gstin": {"text": SUPPLIER_GSTIN_KA, "page": 1, "certain": True},
        "buyer.name": {"text": "Marigold Retail Pvt Ltd", "page": 1, "certain": True},
        "buyer.gstin": {"text": BUYER_GSTIN_KA, "page": 1, "certain": True},
        "place_of_supply": {"text": "29-Karnataka", "page": 1, "certain": True},
        "tax.taxable_amount": {"text": "1,00,000.00", "page": 1, "certain": True},
        "tax.cgst": {"text": "9,000.00", "page": 1, "certain": True},
        "tax.sgst": {"text": "9,000.00", "page": 1, "certain": True},
        "total": {"text": "1,18,000.00", "page": 1, "certain": True},
    },
}


def interstate_provider_output() -> dict[str, Any]:
    """Maharashtra supplier, Tamil Nadu place of supply: IGST, no CGST/SGST."""
    payload = _deep_copy(SAMPLE_PROVIDER_OUTPUT)
    payload["invoice_number"] = "MH/2026-27/0442"
    payload["place_of_supply"] = "33-Tamil Nadu"
    payload["supplier"]["gstin"] = SUPPLIER_GSTIN_MH
    payload["buyer"]["gstin"] = BUYER_GSTIN_TN
    payload["tax"] = {
        "taxable_amount": 100000, "cgst": None, "sgst": None,
        "igst": 18000, "utgst": None, "cess": None,
    }
    for item in payload["items"]:
        item["igst"] = (item["cgst"] or 0) + (item["sgst"] or 0)
        item["cgst"] = item["sgst"] = None
    payload["_evidence"].pop("tax.cgst", None)
    payload["_evidence"].pop("tax.sgst", None)
    payload["_evidence"]["tax.igst"] = {"text": "18,000.00", "page": 1, "certain": True}
    payload["_evidence"]["supplier.gstin"]["text"] = SUPPLIER_GSTIN_MH
    payload["_evidence"]["buyer.gstin"]["text"] = BUYER_GSTIN_TN
    return payload


def sparse_provider_output() -> dict[str, Any]:
    """A B2C cash memo: no buyer GSTIN, no line items, no bank details."""
    return {
        "invoice_number": "CM/2026/0091",
        "invoice_date": "2026-09-18",
        "due_date": None,
        "place_of_supply": "29-Karnataka",
        "reverse_charge": None,
        "supplier": {"name": "Nandini Stores", "gstin": SUPPLIER_GSTIN_KA},
        "buyer": {"name": None, "gstin": None},
        "items": [],
        "subtotal": 1000,
        "total": 1180,
        "currency": "INR",
        "tax": {"taxable_amount": 1000, "cgst": 90, "sgst": 90},
        "_evidence": {
            "invoice_number": {"text": "CM/2026/0091", "page": 1, "certain": True},
        },
    }


def inconsistent_provider_output() -> dict[str, Any]:
    """Totals that do not reconcile, and an invalid buyer GSTIN."""
    payload = _deep_copy(SAMPLE_PROVIDER_OUTPUT)
    payload["total"] = 125000          # should be 118000
    payload["buyer"]["gstin"] = INVALID_GSTIN
    payload["_evidence"]["total"]["text"] = "1,25,000.00"
    payload["_evidence"]["buyer.gstin"]["text"] = INVALID_GSTIN
    return payload


def _deep_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _deep_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_copy(v) for v in value]
    return value


# --- Renderable documents ----------------------------------------------


@dataclass
class InvoiceSpec:
    """The inputs needed to draw a synthetic invoice."""

    invoice_number: str = "INV-29381"
    invoice_date: str = "18/09/2026"
    supplier_name: str = "Udupi Software Systems Pvt Ltd"
    supplier_gstin: str = SUPPLIER_GSTIN_KA
    buyer_name: str = "Marigold Retail Pvt Ltd"
    buyer_gstin: str | None = BUYER_GSTIN_KA
    place_of_supply: str = "29-Karnataka"
    items: list[tuple[str, str, Decimal, Decimal]] = field(
        default_factory=lambda: [
            ("Annual platform licence", "998314", Decimal(1), Decimal(80000)),
            ("Onboarding and configuration", "998313", Decimal(20), Decimal(1000)),
        ]
    )
    interstate: bool = False
    extra_pages: int = 0

    @property
    def taxable_amount(self) -> Decimal:
        return sum((q * p for _, _, q, p in self.items), Decimal(0))

    @property
    def tax_total(self) -> Decimal:
        return self.taxable_amount * Decimal("0.18")

    @property
    def total(self) -> Decimal:
        return self.taxable_amount + self.tax_total


def _money(value: Decimal) -> str:
    """Format in the Indian lakh grouping an invoice would actually print."""
    quantised = f"{value:.2f}"
    whole, _, fraction = quantised.partition(".")
    negative = whole.startswith("-")
    whole = whole.lstrip("-")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return ("-" if negative else "") + f"{whole}.{fraction}"


def build_invoice_pdf(spec: InvoiceSpec | None = None) -> bytes:
    """Draw a synthetic GST invoice as a real, text-bearing PDF."""
    spec = spec or InvoiceSpec()
    builder = PDFBuilder()
    page = builder.new_page()

    page.write(220, 800, "TAX INVOICE", 16, bold=True)
    page.write(40, 770, f"Invoice No: {spec.invoice_number}", 10, bold=True)
    page.write(40, 756, f"Invoice Date: {spec.invoice_date}")
    page.write(40, 742, f"Place of Supply: {spec.place_of_supply}")
    page.write(40, 728, "Reverse Charge: No")

    page.write(40, 700, "Supplier", 11, bold=True)
    page.write(40, 686, spec.supplier_name)
    page.write(40, 672, f"GSTIN: {spec.supplier_gstin}")

    page.write(320, 700, "Bill To", 11, bold=True)
    page.write(320, 686, spec.buyer_name)
    if spec.buyer_gstin:
        page.write(320, 672, f"GSTIN: {spec.buyer_gstin}")

    y = 630
    page.write(40, y, "Description", 9, bold=True)
    page.write(250, y, "HSN/SAC", 9, bold=True)
    page.write(320, y, "Qty", 9, bold=True)
    page.write(370, y, "Rate", 9, bold=True)
    page.write(460, y, "Taxable Value", 9, bold=True)

    for description, hsn, quantity, rate in spec.items:
        y -= 16
        page.write(40, y, description[:34], 9)
        page.write(250, y, hsn, 9)
        page.write(320, y, f"{quantity:g}", 9)
        page.write(370, y, _money(rate), 9)
        page.write(460, y, _money(quantity * rate), 9)

    y -= 34
    page.write(370, y, "Taxable Amount", 9, bold=True)
    page.write(460, y, _money(spec.taxable_amount), 9)
    if spec.interstate:
        y -= 14
        page.write(370, y, "IGST @ 18%", 9)
        page.write(460, y, _money(spec.tax_total), 9)
    else:
        half = spec.tax_total / 2
        y -= 14
        page.write(370, y, "CGST @ 9%", 9)
        page.write(460, y, _money(half), 9)
        y -= 14
        page.write(370, y, "SGST @ 9%", 9)
        page.write(460, y, _money(half), 9)
    y -= 18
    page.write(370, y, "Grand Total", 10, bold=True)
    page.write(460, y, _money(spec.total), 10, bold=True)

    for index in range(spec.extra_pages):
        extra = builder.new_page()
        extra.write(40, 800, f"Continuation sheet {index + 1}", 11, bold=True)
        extra.write(40, 780, "Terms: payment due within 30 days of invoice date.")

    return builder.build()


def build_png(width: int = 900, height: int = 1200) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 40), "TAX INVOICE", fill="black")
    draw.text((40, 70), "Invoice No: INV-29381", fill="black")
    draw.rectangle([30, 30, width - 30, height - 30], outline="black", width=2)
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def build_jpeg() -> bytes:
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (800, 1000), "white").save(buffer, "JPEG")
    return buffer.getvalue()


def build_statement_pdf(*, month: str = "September 2026") -> bytes:
    """A bank statement, as a real text-bearing PDF.

    The classifier reads words, so a statement has to look like one: no
    invoice number, no GSTIN, and the phrases a bank actually prints.
    """
    builder = PDFBuilder()
    page = builder.new_page()

    page.write(180, 800, "STATEMENT OF ACCOUNT", 16, bold=True)
    page.write(40, 770, f"Statement Period: {month}", 10)
    page.write(40, 756, "Account Number: XXXXXXXX4321", 10)
    page.write(40, 742, "IFSC: HDFC0001234", 10)
    page.write(40, 714, "Opening Balance", 10, bold=True)
    page.write(200, 714, "1,42,500.00", 10)

    rows = [
        ("01/09/2026", "NEFT CR SALARY", "", "85,000.00"),
        ("07/09/2026", "UPI DR KIRANA STORE", "2,150.00", ""),
        ("18/09/2026", "CHEQUE DR 004521", "40,000.00", ""),
    ]
    y = 690
    page.write(40, y, "Date", 9, bold=True)
    page.write(120, y, "Narration", 9, bold=True)
    page.write(340, y, "Withdrawal", 9, bold=True)
    page.write(440, y, "Deposit", 9, bold=True)
    for date, narration, withdrawal, deposit in rows:
        y -= 16
        page.write(40, y, date, 9)
        page.write(120, y, narration, 9)
        page.write(340, y, withdrawal, 9)
        page.write(440, y, deposit, 9)

    page.write(40, y - 30, "Closing Balance", 10, bold=True)
    page.write(200, y - 30, "1,85,350.00", 10)
    return builder.build()
