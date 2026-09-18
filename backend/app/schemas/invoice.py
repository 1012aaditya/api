"""The GST invoice schema (§7).

Every field is optional. A field this pipeline could not read off the
document is ``null`` — never a guess, never a plausible default. Callers can
therefore treat a non-null value as "this was on the page", which is the
whole point of the product.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.fields import Money, Quantity


class Party(BaseModel):
    """A supplier or a buyer as printed on the invoice."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    gstin: str | None = None
    pan: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None

    def is_empty(self) -> bool:
        return not any(
            (self.name, self.gstin, self.pan, self.address, self.phone, self.email)
        )


class LineItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: str | None = None
    sku: str | None = None
    hsn_sac: str | None = None
    quantity: Quantity | None = None
    unit: str | None = None
    unit_price: Money | None = None
    discount: Money | None = None
    taxable_value: Money | None = None
    tax_rate: Money | None = Field(default=None, description="Total GST rate, percent.")
    cgst: Money | None = None
    sgst: Money | None = None
    igst: Money | None = None
    cess: Money | None = None
    total: Money | None = None


class TaxBreakdown(BaseModel):
    """Invoice-level tax totals.

    CGST+SGST and IGST are mutually exclusive on a compliant invoice:
    intrastate supply attracts the first pair, interstate the second. The
    validation engine checks this rather than assuming it.
    """

    model_config = ConfigDict(extra="ignore")

    taxable_amount: Money | None = None
    cgst: Money | None = None
    sgst: Money | None = None
    igst: Money | None = None
    utgst: Money | None = None
    cess: Money | None = None


class BankDetails(BaseModel):
    model_config = ConfigDict(extra="ignore")

    account_name: str | None = None
    account_number: str | None = None
    ifsc: str | None = None
    bank_name: str | None = None
    branch: str | None = None


class EInvoiceDetails(BaseModel):
    """IRP-issued e-invoice fields, when the document carries them."""

    model_config = ConfigDict(extra="ignore")

    irn: str | None = None
    ack_number: str | None = None
    ack_date: dt.date | None = None
    qr_code_data: str | None = None


class InvoiceData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document_type: Literal["gst_invoice"] = "gst_invoice"

    # Identity
    invoice_number: str | None = None
    invoice_date: dt.date | None = None
    due_date: dt.date | None = None
    # "invoice" | "credit_note" | "debit_note", when the document says so.
    document_subtype: str | None = None

    # GST specifics
    place_of_supply: str | None = None
    reverse_charge: bool | None = None

    # Parties
    supplier: Party = Field(default_factory=Party)
    buyer: Party = Field(default_factory=Party)
    billing_address: str | None = None
    shipping_address: str | None = None

    # Contents
    items: list[LineItem] = Field(default_factory=list)

    # Money
    subtotal: Money | None = None
    discount: Money | None = None
    other_charges: Money | None = None
    round_off: Money | None = None
    total: Money | None = None
    currency: str | None = "INR"
    tax: TaxBreakdown = Field(default_factory=TaxBreakdown)

    # Payment
    payment_terms: str | None = None
    bank_details: BankDetails | None = None

    # e-invoicing. Mirrored at the top level for convenience (§7).
    e_invoice_details: EInvoiceDetails | None = None
    irn: str | None = None
    qr_code_data: str | None = None
