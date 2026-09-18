"""Typed results.

Two decisions worth knowing about:

**Amounts are ``Decimal``, never ``float``.** This is accounting data. Parsed
as a float, ``118000.50`` can become ``118000.49999999999``, and a reconciliation
that should balance to zero does not. The HTTP layer parses JSON numbers
straight into ``Decimal`` so the exact printed figure survives the trip.

**Every object keeps its ``raw`` dict.** A field the server adds tomorrow is
readable today through ``invoice.raw["new_field"]`` instead of raising. The
dataclass is a convenience over the JSON, not a gate in front of it.

A ``None`` on any field means *the pipeline did not find it on the document* —
never a default, never a guess. That is the whole point of the product, so the
SDK does not paper over it with zeros or empty strings.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

__all__ = [
    "Party",
    "LineItem",
    "TaxBreakdown",
    "Invoice",
    "FieldConfidence",
    "Confidence",
    "ValidationCheck",
    "Validation",
    "Processing",
    "Extraction",
    "Job",
    "Document",
    "RejectedFile",
    "BatchSubmission",
    "Batch",
]


def _money(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):  # bool is an int; never a money value
        return None
    if isinstance(value, int):
        return Decimal(value)
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _date(value: Any) -> Optional[dt.date]:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _datetime(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    text = str(value)
    # Python < 3.11 cannot parse a trailing "Z".
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


@dataclass
class Party:
    """A supplier or buyer, as printed on the invoice."""

    name: Optional[str] = None
    gstin: Optional[str] = None
    pan: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, payload: Any) -> "Party":
        data = _dict(payload)
        return cls(
            name=data.get("name"),
            gstin=data.get("gstin"),
            pan=data.get("pan"),
            address=data.get("address"),
            phone=data.get("phone"),
            email=data.get("email"),
            raw=data,
        )


@dataclass
class LineItem:
    description: Optional[str] = None
    sku: Optional[str] = None
    hsn_sac: Optional[str] = None
    quantity: Optional[Decimal] = None
    unit: Optional[str] = None
    unit_price: Optional[Decimal] = None
    discount: Optional[Decimal] = None
    taxable_value: Optional[Decimal] = None
    tax_rate: Optional[Decimal] = None
    cgst: Optional[Decimal] = None
    sgst: Optional[Decimal] = None
    igst: Optional[Decimal] = None
    cess: Optional[Decimal] = None
    total: Optional[Decimal] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, payload: Any) -> "LineItem":
        data = _dict(payload)
        return cls(
            description=data.get("description"),
            sku=data.get("sku"),
            hsn_sac=data.get("hsn_sac"),
            quantity=_money(data.get("quantity")),
            unit=data.get("unit"),
            unit_price=_money(data.get("unit_price")),
            discount=_money(data.get("discount")),
            taxable_value=_money(data.get("taxable_value")),
            tax_rate=_money(data.get("tax_rate")),
            cgst=_money(data.get("cgst")),
            sgst=_money(data.get("sgst")),
            igst=_money(data.get("igst")),
            cess=_money(data.get("cess")),
            total=_money(data.get("total")),
            raw=data,
        )


@dataclass
class TaxBreakdown:
    """Invoice-level tax totals.

    CGST+SGST and IGST are mutually exclusive on a compliant invoice: intrastate
    supply attracts the first pair, interstate the second. Both being present,
    or neither, is what the server's validation engine looks for.
    """

    taxable_amount: Optional[Decimal] = None
    cgst: Optional[Decimal] = None
    sgst: Optional[Decimal] = None
    igst: Optional[Decimal] = None
    utgst: Optional[Decimal] = None
    cess: Optional[Decimal] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, payload: Any) -> "TaxBreakdown":
        data = _dict(payload)
        return cls(
            taxable_amount=_money(data.get("taxable_amount")),
            cgst=_money(data.get("cgst")),
            sgst=_money(data.get("sgst")),
            igst=_money(data.get("igst")),
            utgst=_money(data.get("utgst")),
            cess=_money(data.get("cess")),
            raw=data,
        )

    @property
    def is_interstate(self) -> Optional[bool]:
        """True for IGST, False for CGST+SGST, None when neither was found.

        ``None`` is not "no" — it means the tax lines were not read off the
        document, so nothing can be concluded either way.
        """
        if self.igst is not None:
            return True
        if self.cgst is not None or self.sgst is not None:
            return False
        return None


@dataclass
class Invoice:
    """The extracted GST invoice. Every field may be ``None``."""

    document_type: str = "gst_invoice"
    invoice_number: Optional[str] = None
    invoice_date: Optional[dt.date] = None
    due_date: Optional[dt.date] = None
    document_subtype: Optional[str] = None
    place_of_supply: Optional[str] = None
    reverse_charge: Optional[bool] = None
    supplier: Party = field(default_factory=Party)
    buyer: Party = field(default_factory=Party)
    billing_address: Optional[str] = None
    shipping_address: Optional[str] = None
    items: List[LineItem] = field(default_factory=list)
    subtotal: Optional[Decimal] = None
    discount: Optional[Decimal] = None
    other_charges: Optional[Decimal] = None
    round_off: Optional[Decimal] = None
    total: Optional[Decimal] = None
    currency: Optional[str] = None
    tax: TaxBreakdown = field(default_factory=TaxBreakdown)
    payment_terms: Optional[str] = None
    irn: Optional[str] = None
    qr_code_data: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, payload: Any) -> "Invoice":
        data = _dict(payload)
        items = data.get("items") or []
        return cls(
            document_type=data.get("document_type") or "gst_invoice",
            invoice_number=data.get("invoice_number"),
            invoice_date=_date(data.get("invoice_date")),
            due_date=_date(data.get("due_date")),
            document_subtype=data.get("document_subtype"),
            place_of_supply=data.get("place_of_supply"),
            reverse_charge=data.get("reverse_charge"),
            supplier=Party.from_json(data.get("supplier")),
            buyer=Party.from_json(data.get("buyer")),
            billing_address=data.get("billing_address"),
            shipping_address=data.get("shipping_address"),
            items=[LineItem.from_json(item) for item in items],
            subtotal=_money(data.get("subtotal")),
            discount=_money(data.get("discount")),
            other_charges=_money(data.get("other_charges")),
            round_off=_money(data.get("round_off")),
            total=_money(data.get("total")),
            currency=data.get("currency"),
            tax=TaxBreakdown.from_json(data.get("tax")),
            payment_terms=data.get("payment_terms"),
            irn=data.get("irn"),
            qr_code_data=data.get("qr_code_data"),
            raw=data,
        )


@dataclass
class FieldConfidence:
    """One field's score, and the evidence behind it."""

    field: str
    confidence: Optional[Decimal] = None
    band: Optional[str] = None
    page: Optional[int] = None
    source_text: Optional[str] = None


@dataclass
class Confidence:
    """Per-field confidence, plus one number for the document.

    Scores are computed from evidence — whether the value was cited on the
    page, whether two sources agreed, whether the format checks out — not
    asserted by a model about itself. Nothing ever scores 1.0.
    """

    overall: Optional[Decimal] = None
    band: Optional[str] = None
    low_confidence_fields: List[str] = field(default_factory=list)
    fields: Dict[str, Any] = field(default_factory=dict, repr=False)
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, payload: Any) -> "Confidence":
        data = _dict(payload)
        return cls(
            overall=_money(data.get("overall")),
            band=data.get("band"),
            low_confidence_fields=list(data.get("low_confidence_fields") or []),
            fields=_dict(data.get("fields")),
            raw=data,
        )

    def field_confidence(self, path: str) -> Optional[FieldConfidence]:
        """The score for one dotted field path, e.g. ``"supplier.gstin"``."""
        entry = self.fields.get(path)
        if not isinstance(entry, dict):
            return None
        return FieldConfidence(
            field=path,
            confidence=_money(entry.get("confidence")),
            band=entry.get("band"),
            page=entry.get("page"),
            source_text=entry.get("source_text"),
        )

    def needs_review(self) -> bool:
        """True when at least one field scored below the medium threshold."""
        return bool(self.low_confidence_fields)


@dataclass
class ValidationCheck:
    name: str
    status: str
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


@dataclass
class Validation:
    """The verdict, and each check behind it.

    ``status`` is one of ``passed``, ``warning``, ``failed`` or ``not_checked``.
    ``not_checked`` is never folded into ``passed``: a check that could not run
    because the data it needs was missing has not vouched for anything.
    """

    overall: str = "not_checked"
    checks: List[ValidationCheck] = field(default_factory=list)
    gstin_format_valid: Optional[bool] = None
    calculation_matches: Optional[bool] = None
    required_fields_present: Optional[bool] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, payload: Any) -> "Validation":
        data = _dict(payload)
        checks = [
            ValidationCheck(
                name=_dict(c).get("name", ""),
                status=_dict(c).get("status", "not_checked"),
                message=_dict(c).get("message"),
                details=_dict(c).get("details") or None,
            )
            for c in (data.get("checks") or [])
        ]
        return cls(
            overall=data.get("overall") or "not_checked",
            checks=checks,
            gstin_format_valid=data.get("gstin_format_valid"),
            calculation_matches=data.get("calculation_matches"),
            required_fields_present=data.get("required_fields_present"),
            raw=data,
        )

    @property
    def passed(self) -> bool:
        """True only for an outright pass. A warning is not a pass."""
        return self.overall == "passed"

    def failures(self) -> List[ValidationCheck]:
        return [c for c in self.checks if c.status == "failed"]

    def warnings(self) -> List[ValidationCheck]:
        return [c for c in self.checks if c.status == "warning"]


@dataclass
class Processing:
    """What it took to produce this result."""

    duration_ms: Optional[int] = None
    pages: Optional[int] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    tiers: List[str] = field(default_factory=list)
    model_called: bool = True
    escalation_reason: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    provider_latency_ms: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    estimated_cost_usd: Optional[Decimal] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, payload: Any) -> "Processing":
        data = _dict(payload)
        return cls(
            duration_ms=data.get("duration_ms"),
            pages=data.get("pages"),
            provider=data.get("provider"),
            model=data.get("model"),
            prompt_version=data.get("prompt_version"),
            tiers=list(data.get("tiers") or []),
            model_called=bool(data.get("model_called", True)),
            escalation_reason=data.get("escalation_reason"),
            notes=list(data.get("notes") or []),
            provider_latency_ms=data.get("provider_latency_ms"),
            input_tokens=data.get("input_tokens"),
            output_tokens=data.get("output_tokens"),
            estimated_cost_usd=_money(data.get("estimated_cost_usd")),
            raw=data,
        )


@dataclass
class Extraction:
    """A complete extraction: the invoice, and how much to trust it."""

    request_id: Optional[str] = None
    document_id: Optional[str] = None
    extraction_id: Optional[str] = None
    data: Invoice = field(default_factory=Invoice)
    confidence: Confidence = field(default_factory=Confidence)
    validation: Validation = field(default_factory=Validation)
    processing: Processing = field(default_factory=Processing)
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, payload: Any) -> "Extraction":
        data = _dict(payload)
        return cls(
            request_id=data.get("request_id"),
            document_id=data.get("document_id"),
            extraction_id=data.get("extraction_id"),
            data=Invoice.from_json(data.get("data")),
            confidence=Confidence.from_json(data.get("confidence")),
            validation=Validation.from_json(data.get("validation")),
            processing=Processing.from_json(data.get("processing")),
            raw=data,
        )

    @classmethod
    def from_stored_json(cls, payload: Any) -> "Extraction":
        """Build from ``GET /v1/documents/{id}/extraction``.

        That endpoint returns a *stored* extraction, whose shape differs from a
        live one: the confidence fields and the overall score sit side by side
        rather than nested, and the processing metadata is flattened. Both come
        back from this library as the same ``Extraction`` so calling code does
        not have to care which way the result was obtained.

        One difference survives: a stored extraction does not carry the
        server's ``low_confidence_fields`` list, so it is derived here from each
        field's own band. The three headline validation booleans are not stored
        either, and stay ``None`` — which means "not recorded", not "false".
        """
        data = _dict(payload)
        fields = _dict(data.get("confidence"))
        low = [
            name
            for name, entry in fields.items()
            if isinstance(entry, dict) and entry.get("band") == "low"
        ]
        confidence = Confidence(
            overall=_money(data.get("overall_confidence")),
            band=None,
            low_confidence_fields=sorted(low),
            fields=fields,
            raw=data,
        )
        processing = Processing(
            duration_ms=data.get("total_latency_ms"),
            provider=data.get("provider"),
            model=data.get("model"),
            prompt_version=data.get("prompt_version"),
            provider_latency_ms=data.get("provider_latency_ms"),
            input_tokens=data.get("input_tokens"),
            output_tokens=data.get("output_tokens"),
            estimated_cost_usd=_money(data.get("estimated_cost_usd")),
            raw=data,
        )
        return cls(
            request_id=data.get("request_id"),
            document_id=data.get("document_id"),
            extraction_id=data.get("id"),
            data=Invoice.from_json(data.get("data")),
            confidence=confidence,
            validation=Validation.from_json(data.get("validation")),
            processing=processing,
            raw=data,
        )

    @property
    def invoice(self) -> Invoice:
        """Alias for ``.data``, for code that reads better that way."""
        return self.data

    def needs_review(self) -> bool:
        """True when a human should look before this is posted to the ledger.

        Deliberately conservative: anything short of a clean validation pass,
        or any field below the confidence floor, is worth a glance.
        """
        return not self.validation.passed or self.confidence.needs_review()


@dataclass
class Job:
    """One background extraction."""

    id: str = ""
    status: str = "queued"
    document_id: str = ""
    document_type: Optional[str] = None
    request_id: Optional[str] = None
    extraction_id: Optional[str] = None
    attempts: int = 0
    max_attempts: int = 0
    error: Optional[Dict[str, str]] = None
    created_at: Optional[dt.datetime] = None
    started_at: Optional[dt.datetime] = None
    completed_at: Optional[dt.datetime] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, payload: Any) -> "Job":
        data = _dict(payload)
        return cls(
            id=data.get("id") or data.get("job_id") or "",
            status=data.get("status") or "queued",
            document_id=data.get("document_id") or "",
            document_type=data.get("document_type"),
            request_id=data.get("request_id"),
            extraction_id=data.get("extraction_id"),
            attempts=data.get("attempts") or 0,
            max_attempts=data.get("max_attempts") or 0,
            error=data.get("error"),
            created_at=_datetime(data.get("created_at")),
            started_at=_datetime(data.get("started_at")),
            completed_at=_datetime(data.get("completed_at")),
            raw=data,
        )

    @property
    def done(self) -> bool:
        return self.status in ("completed", "failed")

    @property
    def succeeded(self) -> bool:
        return self.status == "completed"


@dataclass
class Document:
    id: str = ""
    filename: Optional[str] = None
    status: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    page_count: Optional[int] = None
    batch_id: Optional[str] = None
    created_at: Optional[dt.datetime] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, payload: Any) -> "Document":
        data = _dict(payload)
        return cls(
            id=data.get("id") or "",
            filename=data.get("filename"),
            status=data.get("status"),
            content_type=data.get("content_type"),
            size_bytes=data.get("size_bytes"),
            page_count=data.get("page_count"),
            batch_id=data.get("batch_id"),
            created_at=_datetime(data.get("created_at")),
            raw=data,
        )


@dataclass
class RejectedFile:
    """A file the server refused, and why. Never silently dropped."""

    filename: str
    code: str
    message: str

    @classmethod
    def from_json(cls, payload: Any) -> "RejectedFile":
        data = _dict(payload)
        return cls(
            filename=data.get("filename") or "",
            code=data.get("code") or "",
            message=data.get("message") or "",
        )


@dataclass
class BatchSubmission:
    """The receipt for a bulk upload.

    ``rejected`` is the part worth reading: a file that could not be accepted
    comes back named, with its reason, rather than disappearing.
    """

    batch_id: str = ""
    request_id: Optional[str] = None
    accepted: int = 0
    job_ids: List[str] = field(default_factory=list)
    rejected: List[RejectedFile] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, payload: Any) -> "BatchSubmission":
        data = _dict(payload)
        return cls(
            batch_id=data.get("batch_id") or "",
            request_id=data.get("request_id"),
            accepted=data.get("accepted") or 0,
            job_ids=list(data.get("job_ids") or []),
            rejected=[RejectedFile.from_json(r) for r in (data.get("rejected") or [])],
            raw=data,
        )


@dataclass
class Batch:
    """A batch's progress, counted from its jobs."""

    id: str = ""
    name: Optional[str] = None
    document_count: int = 0
    rejected_count: int = 0
    total: int = 0
    queued: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0
    done: bool = False
    created_at: Optional[dt.datetime] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, payload: Any) -> "Batch":
        data = _dict(payload)
        return cls(
            id=data.get("id") or "",
            name=data.get("name"),
            document_count=data.get("document_count") or 0,
            rejected_count=data.get("rejected_count") or 0,
            total=data.get("total") or 0,
            queued=data.get("queued") or 0,
            processing=data.get("processing") or 0,
            completed=data.get("completed") or 0,
            failed=data.get("failed") or 0,
            done=bool(data.get("done")),
            created_at=_datetime(data.get("created_at")),
            raw=data,
        )
