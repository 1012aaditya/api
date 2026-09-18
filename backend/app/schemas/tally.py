"""Request and response shapes for the Tally integration."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class LedgerOut(BaseModel):
    id: str
    name: str
    gstin: str | None = None
    parent_group: str | None = None


class LedgerImportResult(BaseModel):
    imported: int = Field(..., description="Ledgers now in the master.")
    replaced: int = Field(..., description="Ledgers there were before this import.")
    aliases_kept: int = Field(
        ...,
        description=(
            "Confirmed supplier mappings carried over. Re-importing a master "
            "does not throw away corrections already made."
        ),
    )


class TallySettingsIn(BaseModel):
    """Ledger names to post the non-supplier legs to.

    Names rather than ids, because Tally matches on the name at import time
    and the customer may create a ledger after configuring this.
    """

    company_name: str | None = Field(
        default=None,
        description="Must match the company open in Tally, or the import is refused.",
        max_length=300,
    )
    voucher_type: str = Field(default="Purchase", max_length=100)
    purchase_ledger: str | None = Field(default=None, max_length=300)
    cgst_ledger: str | None = Field(default=None, max_length=300)
    sgst_ledger: str | None = Field(default=None, max_length=300)
    igst_ledger: str | None = Field(default=None, max_length=300)
    utgst_ledger: str | None = Field(default=None, max_length=300)
    cess_ledger: str | None = Field(default=None, max_length=300)
    round_off_ledger: str | None = Field(default=None, max_length=300)
    other_charges_ledger: str | None = Field(default=None, max_length=300)


class TallySettingsOut(TallySettingsIn):
    configured: bool = Field(
        ..., description="Whether the minimum needed to post anything is set."
    )
    ledger_count: int = 0
    unknown_ledgers: list[str] = Field(
        default_factory=list,
        description=(
            "Configured names that are not in the imported master. Tally will "
            "reject a voucher naming a ledger that does not exist."
        ),
    )


class LedgerSuggestion(BaseModel):
    ledger_id: str
    ledger_name: str
    score: float


class UnmatchedSupplier(BaseModel):
    """A supplier nobody can resolve yet, and the best guesses for it."""

    name: str
    gstin: str | None = None
    documents: int
    suggestions: list[LedgerSuggestion] = Field(default_factory=list)


class VoucherPreview(BaseModel):
    document_id: str
    filename: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    supplier_name: str | None = None
    supplier_gstin: str | None = None
    total: str | None = None
    ledger_name: str | None = None
    match_method: str
    postable: bool
    blockers: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TallyPreview(BaseModel):
    """What would be written, and what would not — before anything is posted."""

    postable: int
    blocked: int
    ledger_count: int
    settings_configured: bool
    unmatched_suppliers: list[UnmatchedSupplier] = Field(default_factory=list)
    vouchers: list[VoucherPreview] = Field(default_factory=list)


class ConfirmMatchIn(BaseModel):
    """A human answering 'which ledger is this supplier?'."""

    ledger_id: str
    supplier_name: str | None = Field(default=None, max_length=300)
    supplier_gstin: str | None = Field(default=None, max_length=20)


class AliasOut(BaseModel):
    id: str
    ledger_id: str
    ledger_name: str | None = None
    key_type: str
    match_key: str
    created_at: dt.datetime
