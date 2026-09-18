"""Tiered extraction: read cheaply first, escalate only when needed (§34)."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings, get_settings
from app.pipelines.merge import merge_tiers
from app.pipelines.stages.preprocess import prepare_document
from app.pipelines.stages.qr_extract import extract_from_qr, parse_qr_payload
from app.pipelines.stages.text_extract import extract_from_text_layer
from app.pipelines.tiers import ExtractionTier, TierOutput
from app.schemas.invoice import InvoiceData
from app.services.file_validation import validate_upload
from tests.conftest import StubProvider, Tenant
from tests.fixtures.einvoice import build_einvoice_png, build_qr_jws
from tests.fixtures.invoices import (
    BUYER_GSTIN_TN,
    SUPPLIER_GSTIN_KA,
    InvoiceSpec,
    build_invoice_pdf,
    build_png,
)

ENDPOINT = "/v1/invoices/extract"
LIMITS = {"max_size_bytes": 10 * 1024 * 1024, "max_page_count": 25}


def prepare(content: bytes, name: str = "doc.pdf"):
    return validate_upload(content, filename=name, **LIMITS)


# --- tier 0: the e-invoice QR ------------------------------------------


def test_the_qr_tier_reads_an_einvoice_without_a_model() -> None:
    result = extract_from_qr(prepare(build_einvoice_png(), "einvoice.png"))
    assert result is not None
    assert result.tier is ExtractionTier.QR

    invoice = result.invoice
    assert invoice.invoice_number == "INV-29381"
    assert invoice.invoice_date.isoformat() == "2026-09-18"
    assert invoice.supplier.gstin == SUPPLIER_GSTIN_KA
    assert invoice.buyer.gstin == BUYER_GSTIN_TN
    assert float(invoice.total) == 118000.0
    assert invoice.document_subtype == "invoice"
    assert invoice.irn


def test_the_qr_tier_says_it_did_not_verify_the_signature() -> None:
    """An unverified QR is a strong reading, not proof. It must say so."""
    result = extract_from_qr(prepare(build_einvoice_png(), "einvoice.png"))
    assert result is not None
    assert any("signature was not verified" in note for note in result.notes)


def test_qr_evidence_cites_the_page_it_was_read_from() -> None:
    result = extract_from_qr(prepare(build_einvoice_png(), "einvoice.png"))
    assert result is not None
    assert result.evidence["invoice_number"].page == 1
    assert result.evidence["total"].text == "118000.00"


def test_a_document_without_a_qr_yields_nothing_rather_than_guessing() -> None:
    assert extract_from_qr(prepare(build_invoice_pdf(), "plain.pdf")) is None
    assert extract_from_qr(prepare(build_png(), "blank.png")) is None


def test_credit_and_debit_notes_are_recognised() -> None:
    for code, expected in (("CRN", "credit_note"), ("DBN", "debit_note")):
        result = extract_from_qr(
            prepare(build_einvoice_png(build_qr_jws(DocTyp=code)), "e.png")
        )
        assert result is not None
        assert result.invoice.document_subtype == expected


@pytest.mark.parametrize("text", ["", "hello world", "a.b", "{not json}", "..."])
def test_a_non_einvoice_qr_is_ignored(text: str) -> None:
    assert parse_qr_payload(text) is None


def test_a_bare_json_qr_is_also_accepted() -> None:
    """Some vendors emit the fields as plain JSON rather than a JWS."""
    import json

    from tests.fixtures.einvoice import DEFAULT_PAYLOAD

    parsed = parse_qr_payload(json.dumps(DEFAULT_PAYLOAD))
    assert parsed is not None
    assert parsed.get("doc_no") == "INV-29381"


# --- tier 1: the PDF text layer ----------------------------------------


def test_the_text_layer_tier_reads_a_digital_invoice_without_a_model() -> None:
    document = prepare_document(prepare(build_invoice_pdf()))
    result = extract_from_text_layer(document)
    assert result is not None
    assert result.tier is ExtractionTier.TEXT_LAYER

    invoice = result.invoice
    assert invoice.invoice_number == "INV-29381"
    assert invoice.invoice_date.isoformat() == "2026-09-18"
    assert invoice.place_of_supply == "29-Karnataka"
    assert invoice.reverse_charge is False
    assert float(invoice.total) == 118000.0
    assert float(invoice.tax.taxable_amount) == 100000.0
    assert float(invoice.tax.cgst) == 9000.0
    assert float(invoice.tax.sgst) == 9000.0
    assert invoice.currency == "INR"


def test_the_text_layer_assigns_gstins_by_their_role_labels() -> None:
    content = build_invoice_pdf(
        InvoiceSpec(supplier_gstin=SUPPLIER_GSTIN_KA, buyer_gstin=BUYER_GSTIN_TN)
    )
    result = extract_from_text_layer(prepare_document(prepare(content)))
    assert result is not None
    assert result.invoice.supplier.gstin == SUPPLIER_GSTIN_KA
    assert result.invoice.buyer.gstin == BUYER_GSTIN_TN


def test_a_lone_unlabelled_gstin_is_flagged_rather_than_asserted() -> None:
    """The issuer is always registered, so a header GSTIN is probably theirs.

    "Probably" is not "stated": it is attributed, marked uncertain, and said
    out loud in the notes.
    """
    from tests.fixtures.pdf_builder import PDFBuilder

    builder = PDFBuilder()
    page = builder.new_page()
    page.write(200, 800, "TAX INVOICE", 16, bold=True)
    page.write(40, 770, f"GSTIN: {SUPPLIER_GSTIN_KA}")
    page.write(40, 750, "Invoice No: INV-7")
    page.write(40, 730, "Grand Total 1,180.00")

    result = extract_from_text_layer(prepare_document(prepare(builder.build())))
    assert result is not None
    assert result.invoice.supplier.gstin == SUPPLIER_GSTIN_KA
    assert result.evidence["supplier.gstin"].certain is False
    assert any("marked uncertain" in note for note in result.notes)


def test_the_text_layer_does_not_invent_line_items() -> None:
    """Rebuilding table columns without layout analysis guesses."""
    result = extract_from_text_layer(prepare_document(prepare(build_invoice_pdf())))
    assert result is not None
    assert result.invoice.items == []
    assert any("Line items are not extracted" in note for note in result.notes)


def test_a_scan_has_no_text_layer_so_the_tier_declines() -> None:
    document = prepare_document(prepare(build_png(), "scan.png"))
    assert extract_from_text_layer(document) is None


def test_subtotal_does_not_capture_the_grand_total() -> None:
    """A label match on "Total" must not win inside "Subtotal"."""
    from tests.fixtures.pdf_builder import PDFBuilder

    builder = PDFBuilder()
    page = builder.new_page()
    page.write(40, 800, "Invoice No: INV-9")
    page.write(40, 760, "Sub Total 1,000.00")
    page.write(40, 740, "CGST @ 9% 90.00")
    page.write(40, 720, "SGST @ 9% 90.00")
    page.write(40, 700, "Grand Total 1,180.00")

    result = extract_from_text_layer(prepare_document(prepare(builder.build())))
    assert result is not None
    assert float(result.invoice.total) == 1180.0
    assert float(result.invoice.subtotal) == 1000.0
    # The rate on the line must not be mistaken for the amount.
    assert float(result.invoice.tax.cgst) == 90.0


# --- merging -----------------------------------------------------------


def test_the_more_direct_source_wins_and_the_disagreement_is_recorded() -> None:
    qr = TierOutput(
        ExtractionTier.QR,
        InvoiceData.model_validate({"invoice_number": "INV-1", "total": "118000.00"}),
    )
    model = TierOutput(
        ExtractionTier.MODEL,
        InvoiceData.model_validate(
            {"invoice_number": "INV-1", "total": 125000, "supplier": {"name": "Acme"}}
        ),
    )
    merged = merge_tiers([model, qr])  # deliberately out of order

    assert float(merged.invoice.total) == 118000.0
    assert merged.field_sources["total"] is ExtractionTier.QR
    # The model still contributes what nothing else had.
    assert merged.invoice.supplier.name == "Acme"
    assert merged.field_sources["supplier.name"] is ExtractionTier.MODEL
    # And the disagreement is surfaced rather than buried.
    assert [c.path for c in merged.conflicts] == ["total"]


def test_agreement_between_sources_produces_no_conflict() -> None:
    payload = {"invoice_number": "INV-1", "total": "118000.00"}
    merged = merge_tiers(
        [
            TierOutput(ExtractionTier.QR, InvoiceData.model_validate(payload)),
            TierOutput(ExtractionTier.MODEL, InvoiceData.model_validate(payload)),
        ]
    )
    assert merged.conflicts == []


def test_formatting_differences_are_not_conflicts() -> None:
    merged = merge_tiers(
        [
            TierOutput(
                ExtractionTier.QR, InvoiceData.model_validate({"total": "118000.00"})
            ),
            TierOutput(ExtractionTier.MODEL, InvoiceData.model_validate({"total": 118000})),
        ]
    )
    assert merged.conflicts == []


# --- routing through the API -------------------------------------------


async def test_a_digital_invoice_still_reaches_the_model_for_line_items(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    """With line items required, a table-bearing invoice escalates."""
    use_provider(stub_provider)
    response = await client.post(
        ENDPOINT,
        files={"file": ("i.pdf", build_invoice_pdf(), "application/pdf")},
        headers=tenant.headers,
    )
    assert response.status_code == 200
    assert stub_provider.calls, "expected the model tier to run"
    assert response.json()["data"]["items"]


async def test_the_cheap_tiers_answer_alone_when_line_items_are_not_required(
    app,
    client: httpx.AsyncClient,
    tenant: Tenant,
    use_provider,
    stub_provider: StubProvider,
) -> None:
    """The whole point: a digital invoice costs nothing when header data is enough."""
    relaxed = Settings(
        **{**get_settings().model_dump(), "extraction_require_line_items": False}
    )
    app.dependency_overrides[get_settings] = lambda: relaxed
    try:
        use_provider(stub_provider)
        response = await client.post(
            ENDPOINT,
            files={"file": ("i.pdf", build_invoice_pdf(), "application/pdf")},
            headers=tenant.headers,
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 200
    body = response.json()
    assert stub_provider.calls == [], "the model should not have been called"
    assert body["data"]["invoice_number"] == "INV-29381"
    assert body["data"]["total"] == 118000
    assert body["data"]["tax"]["cgst"] == 9000
    assert body["processing"]["provider"] == "none"
    assert body["processing"]["model"] == "none"
    assert body["processing"]["estimated_cost_usd"] is None


async def test_a_local_only_deployment_reports_gaps_rather_than_guessing(
    app,
    client: httpx.AsyncClient,
    tenant: Tenant,
    use_provider,
    stub_provider: StubProvider,
) -> None:
    """With the model tier switched off, a scan cannot be read — and says so."""
    local_only = Settings(
        **{**get_settings().model_dump(), "extraction_tiers": "qr,text_layer"}
    )
    app.dependency_overrides[get_settings] = lambda: local_only
    try:
        use_provider(stub_provider)
        response = await client.post(
            ENDPOINT,
            files={"file": ("e.png", build_einvoice_png(), "image/png")},
            headers=tenant.headers,
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 200
    body = response.json()
    assert stub_provider.calls == []
    # The QR gave us these.
    assert body["data"]["invoice_number"] == "INV-29381"
    assert body["data"]["supplier"]["gstin"] == SUPPLIER_GSTIN_KA
    # And what it could not give us stays null, with the gap reported.
    assert body["data"]["items"] == []
    assert body["data"]["supplier"]["name"] is None
    assert body["validation"]["overall"] in {"passed", "warning", "failed"}


async def test_the_response_says_which_tiers_ran(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    """An integrator debugging a bill deserves to see where the work went."""
    use_provider(stub_provider)
    body = (
        await client.post(
            ENDPOINT,
            files={"file": ("i.pdf", build_invoice_pdf(), "application/pdf")},
            headers=tenant.headers,
        )
    ).json()

    processing = body["processing"]
    assert processing["tiers"] == ["text_layer", "model"]
    assert processing["model_called"] is True
    assert "line items" in (processing["escalation_reason"] or "")
    assert any("text layer" in note for note in processing["notes"])


async def test_an_einvoice_reports_the_qr_tier(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    body = (
        await client.post(
            ENDPOINT,
            files={"file": ("e.png", build_einvoice_png(), "image/png")},
            headers=tenant.headers,
        )
    ).json()
    assert "qr" in body["processing"]["tiers"]
    assert any("QR signature was not verified" in n for n in body["processing"]["notes"])


async def test_cross_source_agreement_is_reported_as_a_check(
    client: httpx.AsyncClient, tenant: Tenant, use_provider
) -> None:
    """The QR and the model read the same invoice; the report says whether they agreed."""
    from tests.fixtures.invoices import SAMPLE_PROVIDER_OUTPUT

    use_provider(StubProvider(SAMPLE_PROVIDER_OUTPUT))
    body = (
        await client.post(
            ENDPOINT,
            files={"file": ("e.png", build_einvoice_png(), "image/png")},
            headers=tenant.headers,
        )
    ).json()

    check = next(
        c for c in body["validation"]["checks"] if c["name"] == "source_agreement"
    )
    assert check["status"] in {"passed", "warning"}


async def test_a_qr_that_contradicts_the_model_is_surfaced_not_buried(
    client: httpx.AsyncClient, tenant: Tenant, use_provider
) -> None:
    """Two readings, one answer, and the disagreement stated plainly."""
    from tests.fixtures.invoices import SAMPLE_PROVIDER_OUTPUT

    contradicting = {**SAMPLE_PROVIDER_OUTPUT, "total": 999999}
    use_provider(StubProvider(contradicting))
    body = (
        await client.post(
            ENDPOINT,
            files={"file": ("e.png", build_einvoice_png(), "image/png")},
            headers=tenant.headers,
        )
    ).json()

    # The QR is the IRP's own record, so it wins.
    assert body["data"]["total"] == 118000
    check = next(
        c for c in body["validation"]["checks"] if c["name"] == "source_agreement"
    )
    assert check["status"] == "warning"
    assert "total" in check["message"]
    assert any(c["field"] == "total" for c in check["details"]["conflicts"])
    # And the field is marked down rather than presented as certain.
    assert body["confidence"]["fields"]["total"]["band"] != "high"
