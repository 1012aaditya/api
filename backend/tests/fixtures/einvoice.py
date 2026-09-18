"""Synthetic e-invoice QR fixtures.

The payload shape follows the NIC e-invoice QR: a JWS whose middle segment
carries the invoice's key fields. The signature here is a placeholder — these
fixtures exercise decoding and field mapping, not cryptography, and the
application does not verify signatures either (it says so).
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any

from tests.fixtures.invoices import BUYER_GSTIN_TN, SUPPLIER_GSTIN_KA

DEFAULT_PAYLOAD: dict[str, Any] = {
    "SellerGstin": SUPPLIER_GSTIN_KA,
    "BuyerGstin": BUYER_GSTIN_TN,
    "DocNo": "INV-29381",
    "DocTyp": "INV",
    "DocDt": "18/09/2026",
    "TotInvVal": "118000.00",
    "ItemCnt": "2",
    "MainHsnCode": "998314",
    "Irn": "a1b2c3d4e5" * 6,
    "IrnDt": "2026-09-18 11:12:13",
}


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def build_qr_jws(**overrides: Any) -> str:
    payload = {**DEFAULT_PAYLOAD, **overrides}
    return ".".join(
        [
            _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()),
            _b64(json.dumps(payload).encode()),
            _b64(b"placeholder-signature-not-verified"),
        ]
    )


def build_einvoice_png(jws: str | None = None, *, size: tuple[int, int] = (1240, 1754)) -> bytes:
    """A page image carrying an e-invoice QR, as a scanner would produce."""
    import segno
    from PIL import Image

    buffer = io.BytesIO()
    segno.make(jws or build_qr_jws(), error="m").save(
        buffer, kind="png", scale=4, border=4
    )
    with Image.open(buffer) as qr:
        qr = qr.convert("RGB")
        page = Image.new("RGB", size, "white")
        page.paste(qr, (size[0] - qr.width - 60, 80))
        out = io.BytesIO()
        page.save(out, "PNG")
    return out.getvalue()
