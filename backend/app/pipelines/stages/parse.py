"""Provider JSON → typed invoice (§7, §11).

The model is asked for a precise shape and mostly returns it, but "mostly" is
not a contract. This stage coerces what it can, drops what it cannot read,
and records every drop so the confidence scorer and the logs know the payload
was not clean. A field that cannot be coerced becomes null — it is never
replaced with a substitute value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from app.core.errors import ExtractionFailedError
from app.core.logging import get_logger
from app.schemas.fields import _to_decimal
from app.schemas.invoice import InvoiceData
from app.utils.dates import parse_date

logger = get_logger("docuparse.parse")

EVIDENCE_KEY = "_evidence"

_DATE_FIELDS = ("invoice_date", "due_date")
_PARTY_FIELDS = ("supplier", "buyer")
_MONEY_FIELDS = ("subtotal", "discount", "other_charges", "round_off", "total")
_TAX_FIELDS = ("taxable_amount", "cgst", "sgst", "igst", "utgst", "cess")
_ITEM_MONEY_FIELDS = (
    "quantity", "unit_price", "discount", "taxable_value",
    "tax_rate", "cgst", "sgst", "igst", "cess", "total",
)


@dataclass(frozen=True)
class FieldEvidence:
    """The model's citation for one field: what it read, and where."""

    text: str | None = None
    page: int | None = None
    certain: bool = True


@dataclass
class ParsedExtraction:
    invoice: InvoiceData
    evidence: dict[str, FieldEvidence] = field(default_factory=dict)
    # Fields present in the provider output that could not be coerced. These
    # are nulled rather than guessed, and they lower confidence.
    dropped_fields: list[str] = field(default_factory=list)
    ambiguous_fields: list[str] = field(default_factory=list)


def _clean_string(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    if not text:
        return None
    # Models sometimes emit these instead of null.
    if text.lower() in {"null", "none", "n/a", "na", "-", "--", "not available", "not found"}:
        return None
    return " ".join(text.split())


def _clean_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"yes", "true", "y"}:
            return True
        if lowered in {"no", "false", "n"}:
            return False
    return None


def _clean_money(value: Any, path: str, dropped: list[str]) -> Decimal | None:
    if value is None:
        return None
    try:
        return _to_decimal(value)
    except (ValueError, TypeError):
        dropped.append(path)
        return None


def _clean_date(value: Any, path: str, dropped: list[str], ambiguous: list[str]) -> str | None:
    if value is None:
        return None
    parsed = parse_date(value)
    if parsed.value is None:
        if _clean_string(value):
            dropped.append(path)
        return None
    if parsed.ambiguous:
        ambiguous.append(path)
    return parsed.value.isoformat()


def _clean_party(raw: Any, dropped: list[str], prefix: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        if raw is not None:
            dropped.append(prefix)
        return {}
    return {
        key: _clean_string(raw.get(key))
        for key in ("name", "gstin", "pan", "address", "phone", "email")
    }


def _clean_items(raw: Any, dropped: list[str], ambiguous: list[str]) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        dropped.append("items")
        return []

    items: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            dropped.append(f"items[{index}]")
            continue
        cleaned: dict[str, Any] = {
            "description": _clean_string(entry.get("description")),
            "sku": _clean_string(entry.get("sku")),
            "hsn_sac": _clean_string(entry.get("hsn_sac")),
            "unit": _clean_string(entry.get("unit")),
        }
        for money_field in _ITEM_MONEY_FIELDS:
            cleaned[money_field] = _clean_money(
                entry.get(money_field), f"items[{index}].{money_field}", dropped
            )
        # A row with nothing on it is noise from the table parse, not an item.
        if any(value is not None for value in cleaned.values()):
            items.append(cleaned)
    return items


def _clean_evidence(raw: Any) -> dict[str, FieldEvidence]:
    if not isinstance(raw, dict):
        return {}
    evidence: dict[str, FieldEvidence] = {}
    for path, entry in raw.items():
        if not isinstance(path, str):
            continue
        if isinstance(entry, str):
            evidence[path] = FieldEvidence(text=_clean_string(entry))
            continue
        if not isinstance(entry, dict):
            continue
        page = entry.get("page")
        evidence[path] = FieldEvidence(
            text=_clean_string(entry.get("text")),
            page=page if isinstance(page, int) and page > 0 else None,
            certain=entry.get("certain") is not False,
        )
    return evidence


def parse_provider_output(raw: dict[str, Any]) -> ParsedExtraction:
    if not isinstance(raw, dict):
        raise ExtractionFailedError("The extraction model did not return a JSON object.")

    dropped: list[str] = []
    ambiguous: list[str] = []

    evidence = _clean_evidence(raw.get(EVIDENCE_KEY))

    payload: dict[str, Any] = {
        "invoice_number": _clean_string(raw.get("invoice_number")),
        "document_subtype": _clean_string(raw.get("document_subtype")),
        "place_of_supply": _clean_string(raw.get("place_of_supply")),
        "reverse_charge": _clean_bool(raw.get("reverse_charge")),
        "billing_address": _clean_string(raw.get("billing_address")),
        "shipping_address": _clean_string(raw.get("shipping_address")),
        "payment_terms": _clean_string(raw.get("payment_terms")),
        "currency": _clean_string(raw.get("currency")),
        "irn": _clean_string(raw.get("irn")),
        "qr_code_data": _clean_string(raw.get("qr_code_data")),
        "items": _clean_items(raw.get("items"), dropped, ambiguous),
    }

    for date_field in _DATE_FIELDS:
        payload[date_field] = _clean_date(raw.get(date_field), date_field, dropped, ambiguous)

    for party in _PARTY_FIELDS:
        payload[party] = _clean_party(raw.get(party), dropped, party)

    for money_field in _MONEY_FIELDS:
        payload[money_field] = _clean_money(raw.get(money_field), money_field, dropped)

    raw_tax = raw.get("tax") if isinstance(raw.get("tax"), dict) else {}
    payload["tax"] = {
        tax_field: _clean_money(raw_tax.get(tax_field), f"tax.{tax_field}", dropped)
        for tax_field in _TAX_FIELDS
    }

    raw_bank = raw.get("bank_details") if isinstance(raw.get("bank_details"), dict) else None
    payload["bank_details"] = (
        {
            key: _clean_string(raw_bank.get(key))
            for key in ("account_name", "account_number", "ifsc", "bank_name", "branch")
        }
        if raw_bank
        else None
    )

    raw_einv = (
        raw.get("e_invoice_details")
        if isinstance(raw.get("e_invoice_details"), dict)
        else None
    )
    payload["e_invoice_details"] = (
        {
            "irn": _clean_string(raw_einv.get("irn")),
            "ack_number": _clean_string(raw_einv.get("ack_number")),
            "ack_date": _clean_date(
                raw_einv.get("ack_date"), "e_invoice_details.ack_date", dropped, ambiguous
            ),
            "qr_code_data": _clean_string(raw_einv.get("qr_code_data")),
        }
        if raw_einv
        else None
    )

    try:
        invoice = InvoiceData.model_validate(payload)
    except ValidationError as exc:
        # Every scalar was already coerced above, so reaching here means the
        # output was structurally unusable rather than merely messy.
        logger.warning(
            "parse.schema_validation_failed",
            error_count=exc.error_count(),
            locations=[".".join(str(p) for p in e["loc"]) for e in exc.errors()[:5]],
        )
        raise ExtractionFailedError(
            "The extracted data did not match the invoice schema."
        ) from exc

    if dropped:
        logger.info("parse.fields_dropped", count=len(dropped), fields=dropped[:20])

    return ParsedExtraction(
        invoice=invoice,
        evidence=evidence,
        dropped_fields=dropped,
        ambiguous_fields=ambiguous,
    )
