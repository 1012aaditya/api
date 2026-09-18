"""Building Tally purchase vouchers from extracted invoices.

Tally's import format has three conventions that are easy to get wrong and
silently corrupting when you do:

* **Dates are ``YYYYMMDD``** with no separators.
* **``ISDEEMEDPOSITIVE=Yes`` means debit**, and a debit's ``AMOUNT`` is
  *negative*. Credits are ``No`` and positive.
* **Every voucher's amounts must sum to zero.** Tally will reject or mangle
  one that does not.

So a purchase invoice becomes: the purchase ledger and each input-tax ledger
debited, the supplier credited with the invoice total.

The rule this module exists to enforce is the last one. An invoice whose parts
do not add up to its total is not posted — it is reported. A voucher that is
merely *plausible* is worse than no voucher: a gap is something a bookkeeper
notices at month end, and a wrong number is something that quietly reconciles
to a lie.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from app.models import TallySettings
from app.services.tally.matching import LedgerMatch, LedgerMatcher

ZERO = Decimal("0")

#: How far an invoice's parts may miss its printed total before we refuse to
#: post it. One rupee covers ordinary GST rounding; anything larger means a
#: field was misread, and guessing which one is not our job.
DEFAULT_ROUNDING_TOLERANCE = Decimal("1.00")


def money(value: Any) -> Decimal:
    """Coerce an extracted amount to Decimal. Absent and unreadable both → 0."""
    if value is None or isinstance(value, bool):
        return ZERO
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return ZERO


def _q(value: Decimal) -> str:
    """Two decimal places, which is what Tally stores."""
    return f"{value.quantize(Decimal('0.01')):f}"


def tally_date(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value.date().strftime("%Y%m%d")
    if isinstance(value, dt.date):
        return value.strftime("%Y%m%d")
    try:
        return dt.date.fromisoformat(str(value)[:10]).strftime("%Y%m%d")
    except ValueError:
        return None


@dataclass
class LedgerEntry:
    """One leg of the voucher."""

    ledger_name: str
    amount: Decimal
    is_debit: bool

    def to_xml(self, indent: str = "      ") -> str:
        # Debits are negative in Tally's XML; credits positive.
        signed = -self.amount if self.is_debit else self.amount
        return (
            f"{indent}<ALLLEDGERENTRIES.LIST>\n"
            f"{indent}  <LEDGERNAME>{escape(self.ledger_name)}</LEDGERNAME>\n"
            f"{indent}  <ISDEEMEDPOSITIVE>{'Yes' if self.is_debit else 'No'}"
            f"</ISDEEMEDPOSITIVE>\n"
            f"{indent}  <AMOUNT>{_q(signed)}</AMOUNT>\n"
            f"{indent}</ALLLEDGERENTRIES.LIST>\n"
        )


@dataclass
class Voucher:
    """A purchase voucher that is ready to post, or the reason it is not."""

    document_id: str
    filename: str | None
    invoice_number: str | None
    invoice_date: str | None
    supplier_name: str | None
    supplier_gstin: str | None
    total: Decimal
    match: LedgerMatch
    entries: list[LedgerEntry] = field(default_factory=list)
    #: Empty means postable. Anything here means it is held back.
    blockers: list[str] = field(default_factory=list)
    #: Things that are fine but a human should know, e.g. a derived round-off.
    notes: list[str] = field(default_factory=list)
    place_of_supply: str | None = None
    narration: str | None = None
    voucher_type: str = "Purchase"

    @property
    def postable(self) -> bool:
        return not self.blockers and bool(self.entries)

    @property
    def imbalance(self) -> Decimal:
        total = sum(
            (-entry.amount if entry.is_debit else entry.amount) for entry in self.entries
        )
        return Decimal(total or 0)

    def to_xml(self) -> str:
        head = (
            f'     <VOUCHER VCHTYPE={quoteattr(self.voucher_type)} ACTION="Create" '
            f'OBJVIEW="Accounting Voucher View">\n'
        )
        body = [
            f"      <DATE>{self.invoice_date}</DATE>\n",
            f"      <EFFECTIVEDATE>{self.invoice_date}</EFFECTIVEDATE>\n",
            f"      <VOUCHERTYPENAME>{escape(self.voucher_type)}</VOUCHERTYPENAME>\n",
            f"      <VOUCHERNUMBER>{escape(self.invoice_number or '')}</VOUCHERNUMBER>\n",
            f"      <REFERENCE>{escape(self.invoice_number or '')}</REFERENCE>\n",
            f"      <REFERENCEDATE>{self.invoice_date}</REFERENCEDATE>\n",
            f"      <PARTYLEDGERNAME>{escape(self.match.ledger_name or '')}</PARTYLEDGERNAME>\n",
            f"      <PARTYNAME>{escape(self.match.ledger_name or '')}</PARTYNAME>\n",
            "      <PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>\n",
            # A stable id so re-importing the same file updates rather than
            # duplicating. Tally keys on this within a company.
            f"      <REMOTEID>{escape(self.document_id)}</REMOTEID>\n",
        ]
        if self.supplier_gstin:
            body.append(f"      <PARTYGSTIN>{escape(self.supplier_gstin)}</PARTYGSTIN>\n")
        if self.place_of_supply:
            body.append(
                f"      <PLACEOFSUPPLY>{escape(self.place_of_supply)}</PLACEOFSUPPLY>\n"
            )
        if self.narration:
            body.append(f"      <NARRATION>{escape(self.narration)}</NARRATION>\n")

        legs = "".join(entry.to_xml() for entry in self.entries)
        return head + "".join(body) + legs + "     </VOUCHER>\n"


def build_voucher(
    *,
    document_id: str,
    filename: str | None,
    invoice: dict[str, Any],
    settings: TallySettings,
    matcher: LedgerMatcher,
    rounding_tolerance: Decimal = DEFAULT_ROUNDING_TOLERANCE,
) -> Voucher:
    """Turn one extracted invoice into a voucher, or into its blockers."""
    tax = invoice.get("tax") or {}
    supplier = invoice.get("supplier") or {}

    supplier_name = supplier.get("name")
    supplier_gstin = supplier.get("gstin")
    match = matcher.match(name=supplier_name, gstin=supplier_gstin)

    total = money(invoice.get("total"))
    date = tally_date(invoice.get("invoice_date"))

    voucher = Voucher(
        document_id=document_id,
        filename=filename,
        invoice_number=invoice.get("invoice_number"),
        invoice_date=date,
        supplier_name=supplier_name,
        supplier_gstin=supplier_gstin,
        total=total,
        match=match,
        place_of_supply=invoice.get("place_of_supply"),
        voucher_type=settings.voucher_type or "Purchase",
    )

    # --- what must be true before anything is posted -------------------

    if not settings.purchase_ledger:
        voucher.blockers.append(
            "No purchase ledger is configured. Set one in the Tally settings."
        )
    if not match.matched:
        voucher.blockers.append(
            f"Supplier {supplier_name or '(no name on the invoice)'!s} does not "
            "match a ledger. Confirm the ledger to post this one."
        )
    if date is None:
        voucher.blockers.append("No invoice date was read, and Tally needs one.")
    if not voucher.invoice_number:
        voucher.blockers.append("No invoice number was read, and Tally needs one.")
    if total <= ZERO:
        voucher.blockers.append("No invoice total was read, so there is nothing to post.")

    if voucher.blockers:
        return voucher

    # --- the legs ------------------------------------------------------

    taxable = money(tax.get("taxable_amount")) or money(invoice.get("subtotal"))
    if taxable <= ZERO:
        voucher.blockers.append(
            "No taxable value or subtotal was read, so the purchase leg cannot "
            "be worked out."
        )
        return voucher

    tax_legs: list[tuple[str, str | None, Decimal]] = [
        ("CGST", settings.cgst_ledger, money(tax.get("cgst"))),
        ("SGST", settings.sgst_ledger, money(tax.get("sgst"))),
        ("IGST", settings.igst_ledger, money(tax.get("igst"))),
        ("UTGST", settings.utgst_ledger, money(tax.get("utgst"))),
        ("Cess", settings.cess_ledger, money(tax.get("cess"))),
    ]
    for label, ledger_name, amount in tax_legs:
        if amount > ZERO and not ledger_name:
            voucher.blockers.append(
                f"The invoice charges {label} of {_q(amount)} but no {label} "
                "ledger is configured."
            )

    other_charges = money(invoice.get("other_charges"))
    if other_charges != ZERO and not settings.other_charges_ledger:
        voucher.blockers.append(
            f"The invoice has other charges of {_q(other_charges)} but no ledger "
            "is configured for them."
        )

    if voucher.blockers:
        return voucher

    debits: list[LedgerEntry] = [
        LedgerEntry(settings.purchase_ledger or "", taxable, is_debit=True)
    ]
    for _label, ledger_name, amount in tax_legs:
        if amount > ZERO and ledger_name:
            debits.append(LedgerEntry(ledger_name, amount, is_debit=True))
    if other_charges != ZERO and settings.other_charges_ledger:
        debits.append(
            LedgerEntry(settings.other_charges_ledger, other_charges, is_debit=True)
        )

    # --- must it balance ------------------------------------------------

    round_off = money(invoice.get("round_off"))
    posted = sum((entry.amount for entry in debits), ZERO) + round_off
    residual = total - posted

    if residual != ZERO:
        if abs(residual) > rounding_tolerance:
            voucher.blockers.append(
                f"The parts do not add up to the total: {_q(posted)} against a "
                f"printed total of {_q(total)}, a difference of {_q(residual)}. "
                "A field was probably misread — this one needs a human."
            )
            return voucher
        if not settings.round_off_ledger:
            voucher.blockers.append(
                f"A rounding difference of {_q(residual)} needs somewhere to go, "
                "but no round-off ledger is configured."
            )
            return voucher
        round_off += residual
        voucher.notes.append(
            f"A difference of {_q(residual)} was posted to "
            f"{settings.round_off_ledger} as rounding."
        )

    if round_off != ZERO:
        if not settings.round_off_ledger:
            voucher.blockers.append(
                f"The invoice shows a round-off of {_q(round_off)} but no round-off "
                "ledger is configured."
            )
            return voucher
        debits.append(
            LedgerEntry(
                settings.round_off_ledger, abs(round_off), is_debit=round_off > ZERO
            )
        )

    voucher.entries = debits + [
        LedgerEntry(match.ledger_name or "", total, is_debit=False)
    ]

    # Belt and braces: whatever the arithmetic above did, the legs themselves
    # must cancel. If they do not, that is our bug, not the invoice's.
    if voucher.imbalance != ZERO:
        voucher.entries = []
        voucher.blockers.append(
            f"Internal error: the voucher legs did not balance "
            f"(off by {_q(voucher.imbalance)}). Not posted."
        )

    narration_bits = [
        f"{voucher.invoice_number}" if voucher.invoice_number else None,
        supplier_name,
        filename,
    ]
    voucher.narration = " · ".join(bit for bit in narration_bits if bit) or None
    return voucher


def render_envelope(vouchers: Sequence[Voucher], *, company_name: str | None) -> str:
    """Wrap postable vouchers in the envelope Tally's import expects.

    Only postable ones are included. A blocked voucher must never reach this
    function's output — that is the whole contract.
    """
    header = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<ENVELOPE>\n"
        " <HEADER>\n"
        "  <TALLYREQUEST>Import Data</TALLYREQUEST>\n"
        " </HEADER>\n"
        " <BODY>\n"
        "  <IMPORTDATA>\n"
        "   <REQUESTDESC>\n"
        "    <REPORTNAME>Vouchers</REPORTNAME>\n"
    )
    if company_name:
        header += (
            "    <STATICVARIABLES>\n"
            f"     <SVCURRENTCOMPANY>{escape(company_name)}</SVCURRENTCOMPANY>\n"
            "    </STATICVARIABLES>\n"
        )
    header += "   </REQUESTDESC>\n   <REQUESTDATA>\n"

    body = "".join(
        '    <TALLYMESSAGE xmlns:UDF="TallyUDF">\n'
        + voucher.to_xml()
        + "    </TALLYMESSAGE>\n"
        for voucher in vouchers
        if voucher.postable
    )

    return header + body + "   </REQUESTDATA>\n  </IMPORTDATA>\n </BODY>\n</ENVELOPE>\n"


def summarize(vouchers: Iterable[Voucher]) -> dict[str, Any]:
    """Counts for the review screen."""
    postable = blocked = 0
    unmatched_suppliers: dict[str, dict[str, Any]] = {}
    for voucher in vouchers:
        if voucher.postable:
            postable += 1
        else:
            blocked += 1
        if not voucher.match.matched and voucher.supplier_name:
            entry = unmatched_suppliers.setdefault(
                voucher.supplier_name,
                {
                    "name": voucher.supplier_name,
                    "gstin": voucher.supplier_gstin,
                    "documents": 0,
                    "suggestions": [
                        {"ledger_id": s[0], "ledger_name": s[1], "score": round(s[2], 3)}
                        for s in voucher.match.suggestions
                    ],
                },
            )
            entry["documents"] += 1
    return {
        "postable": postable,
        "blocked": blocked,
        "unmatched_suppliers": sorted(
            unmatched_suppliers.values(), key=lambda row: -row["documents"]
        ),
    }
