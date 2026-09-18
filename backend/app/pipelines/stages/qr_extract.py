"""Tier 0 — read the e-invoice QR code (§14).

Indian e-invoices registered with an Invoice Registration Portal carry a QR
code containing a JWS whose payload holds the invoice's key fields: both
GSTINs, the document number and date, the total value, the item count, the
main HSN code and the IRN. Where that QR is present this is the fastest and
most accurate source available — it is the IRP's own record of the invoice,
not somebody's reading of the paper.

**What this does not do: verify the signature.** Doing that needs the IRP's
public key, which this deployment does not ship. So a decoded QR is treated
as a very strong extraction, not as proof — the values still go through
validation, and a QR that disagrees with the printed document is reported
rather than silently preferred. Set ``EINVOICE_QR_PUBLIC_KEY_PATH`` to turn
on real verification.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from app.core.logging import get_logger
from app.pipelines.stages.parse import FieldEvidence
from app.pipelines.tiers import ExtractionTier, TierOutput
from app.schemas.fields import _to_decimal
from app.schemas.invoice import EInvoiceDetails, InvoiceData, Party, TaxBreakdown
from app.services.file_validation import ValidatedFile
from app.utils.dates import parse_date

logger = get_logger("docuparse.qr")

# NIC's field names. Matched case-insensitively because implementations vary
# in casing, and read tolerantly because a missing key is normal.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "seller_gstin": ("sellergstin", "selergstin"),
    "buyer_gstin": ("buyergstin",),
    "doc_no": ("docno", "docnum"),
    "doc_type": ("doctyp", "doctype"),
    "doc_date": ("docdt", "docdate"),
    "total_value": ("totinvval", "totinvvalue"),
    "item_count": ("itemcnt", "itemcount"),
    "main_hsn": ("mainhsncode", "hsncode"),
    "irn": ("irn",),
    "irn_date": ("irndt", "irndate"),
}

_DOC_TYPES = {"INV": "invoice", "CRN": "credit_note", "DBN": "debit_note"}


@dataclass(frozen=True)
class EInvoiceQR:
    raw: str
    payload: dict[str, Any]
    signature_verified: bool = False

    def get(self, name: str) -> Any:
        aliases = _FIELD_ALIASES.get(name, (name,))
        lowered = {str(k).lower(): v for k, v in self.payload.items()}
        for alias in aliases:
            value = lowered.get(alias)
            if value not in (None, ""):
                return value
        return None


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def parse_qr_payload(text: str) -> EInvoiceQR | None:
    """Read an e-invoice QR. Accepts a JWS, or bare JSON where a vendor emits it."""
    candidate = (text or "").strip()
    if not candidate:
        return None

    parts = candidate.split(".")
    if len(parts) == 3:
        try:
            payload = json.loads(_b64url_decode(parts[1]))
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None
        # Some IRPs wrap the invoice fields in a "data" string.
        if isinstance(payload, dict) and isinstance(payload.get("data"), str):
            try:
                payload = json.loads(payload["data"])
            except json.JSONDecodeError:
                pass
        if isinstance(payload, dict):
            return EInvoiceQR(raw=candidate, payload=payload)
        return None

    if candidate.startswith("{"):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            return EInvoiceQR(raw=candidate, payload=payload)
    return None


# QR decoding gets its own render, at a higher resolution and without lossy
# compression. The page images in PreparedDocument are tuned for a vision
# model — capped at 2200px and JPEG-encoded — and that is exactly the wrong
# input here: JPEG ringing around the module edges is what breaks a decode.
QR_RENDER_DPI = 220
QR_MAX_PAGES = 3


def _render_pages_for_qr(file: ValidatedFile, *, max_pages: int) -> list[tuple[int, Any]]:
    """Lossless page images, only for the first few pages."""
    from PIL import Image, ImageOps

    if not file.is_pdf:
        with Image.open(BytesIO(file.content)) as image:
            return [(1, ImageOps.exif_transpose(image).convert("RGB"))]

    import pypdfium2 as pdfium

    pages: list[tuple[int, Any]] = []
    document = pdfium.PdfDocument(BytesIO(file.content))
    try:
        for index in range(min(len(document), max_pages)):
            page = document[index]
            try:
                bitmap = page.render(scale=QR_RENDER_DPI / 72)
                pages.append((index + 1, bitmap.to_pil().convert("RGB")))
            finally:
                page.close()
    finally:
        document.close()
    return pages


def scan_file(file: ValidatedFile, *, max_pages: int = QR_MAX_PAGES) -> list[tuple[int, str]]:
    """Decode every QR on the first few pages. Returns (page number, text)."""
    try:
        import zxingcpp
    except ImportError:  # pragma: no cover - dependency is declared
        logger.warning("qr.decoder_unavailable")
        return []

    try:
        rendered = _render_pages_for_qr(file, max_pages=max_pages)
    except Exception:  # noqa: BLE001 — a file that will not render has no QR
        logger.debug("qr.render_failed")
        return []

    found: list[tuple[int, str]] = []
    for page_number, image in rendered:
        try:
            results = zxingcpp.read_barcodes(image)
        except Exception:  # noqa: BLE001 — a page that will not decode is normal
            logger.debug("qr.page_decode_failed", page=page_number)
            continue
        found.extend((page_number, r.text) for r in results if r.text)
    return found


def extract_from_qr(file: ValidatedFile) -> TierOutput | None:
    """Build a partial invoice from the first e-invoice QR on the document."""
    for page_number, text in scan_file(file):
        qr = parse_qr_payload(text)
        if qr is None or qr.get("doc_no") is None:
            continue
        return _to_tier_output(qr, page_number)
    return None


def _to_tier_output(qr: EInvoiceQR, page_number: int) -> TierOutput:
    populated: set[str] = set()
    evidence: dict[str, FieldEvidence] = {}

    def cite(path: str, printed: Any) -> None:
        populated.add(path)
        evidence[path] = FieldEvidence(
            text=str(printed), page=page_number, certain=True
        )

    values: dict[str, Any] = {}

    if (doc_no := qr.get("doc_no")) is not None:
        values["invoice_number"] = str(doc_no).strip()
        cite("invoice_number", doc_no)

    if (doc_date := qr.get("doc_date")) is not None:
        parsed = parse_date(str(doc_date))
        if parsed.value is not None:
            values["invoice_date"] = parsed.value
            cite("invoice_date", doc_date)

    if (doc_type := qr.get("doc_type")) is not None:
        subtype = _DOC_TYPES.get(str(doc_type).strip().upper())
        if subtype:
            values["document_subtype"] = subtype
            populated.add("document_subtype")

    supplier = Party()
    if (seller := qr.get("seller_gstin")) is not None:
        supplier = Party(gstin=str(seller).strip().upper())
        cite("supplier.gstin", seller)
    values["supplier"] = supplier

    buyer = Party()
    if (buyer_gstin := qr.get("buyer_gstin")) is not None:
        buyer = Party(gstin=str(buyer_gstin).strip().upper())
        cite("buyer.gstin", buyer_gstin)
    values["buyer"] = buyer

    if (total := qr.get("total_value")) is not None:
        try:
            values["total"] = _to_decimal(total)
            cite("total", total)
        except (ValueError, TypeError):
            pass

    irn = qr.get("irn")
    irn_date = parse_date(str(qr.get("irn_date") or "")).value
    if irn:
        values["irn"] = str(irn).strip()
        values["e_invoice_details"] = EInvoiceDetails(
            irn=str(irn).strip(), ack_date=irn_date, qr_code_data=qr.raw
        )
        cite("irn", irn)
    values["qr_code_data"] = qr.raw
    values["tax"] = TaxBreakdown()
    values["currency"] = "INR"

    notes = [f"Read from the e-invoice QR on page {page_number}."]
    if not qr.signature_verified:
        # Said out loud rather than implied: an unverified QR is a very good
        # reading of the document, not a guarantee about it.
        notes.append(
            "The QR signature was not verified — no IRP public key is configured, "
            "so these values are treated as a strong reading, not as proof."
        )

    main_hsn = qr.get("main_hsn")
    if main_hsn:
        notes.append(f"QR reports main HSN {main_hsn}.")
    item_count = qr.get("item_count")
    if item_count:
        notes.append(f"QR reports {item_count} line item(s).")

    logger.info(
        "qr.extracted",
        page=page_number,
        fields=len(populated),
        signature_verified=qr.signature_verified,
    )
    return TierOutput(
        tier=ExtractionTier.QR,
        invoice=InvoiceData.model_validate(values),
        evidence=evidence,
        populated_paths=populated,
        notes=notes,
    )
