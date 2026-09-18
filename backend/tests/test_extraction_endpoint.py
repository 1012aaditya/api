"""POST /v1/invoices/extract — the end-to-end contract (§1, §5, §42)."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.core.errors import ExtractionFailedError, ProviderUnavailableError
from app.db.session import get_session_factory
from app.models import Document, Extraction, UsageEvent, ValidationResult
from tests.conftest import StubProvider, Tenant
from tests.fixtures.invoices import (
    InvoiceSpec,
    build_invoice_pdf,
    build_png,
    inconsistent_provider_output,
    interstate_provider_output,
    sparse_provider_output,
)

ENDPOINT = "/v1/invoices/extract"


def upload(content: bytes = b"", name: str = "invoice.pdf", mime: str = "application/pdf"):
    return {"file": (name, content or build_invoice_pdf(), mime)}


# --- the happy path ----------------------------------------------------


async def test_extracts_a_gst_invoice_end_to_end(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    response = await client.post(ENDPOINT, files=upload(), headers=tenant.headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["success"] is True
    assert body["request_id"].startswith("req_")
    assert body["document_id"].startswith("doc_")
    assert body["extraction_id"].startswith("ext_")

    data = body["data"]
    assert data["document_type"] == "gst_invoice"
    assert data["invoice_number"] == "INV-29381"
    assert data["invoice_date"] == "2026-09-18"
    assert data["supplier"]["gstin"] == "29AABCU9603R1ZJ"
    assert data["total"] == 118000
    assert data["tax"]["cgst"] == 9000
    assert data["currency"] == "INR"
    assert len(data["items"]) == 2

    assert body["validation"]["overall"] == "passed"
    assert body["validation"]["gstin_format_valid"] is True
    assert body["validation"]["calculation_matches"] is True
    assert body["validation"]["required_fields_present"] is True

    assert body["confidence"]["overall"] > 0
    assert body["confidence"]["band"] in {"high", "medium", "low"}
    assert body["processing"]["pages"] == 1
    assert body["processing"]["model"] == "stub-model-v1"
    assert body["processing"]["prompt_version"] == "gst_invoice.v1"


async def test_amounts_are_plain_json_numbers(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    raw = (await client.post(ENDPOINT, files=upload(), headers=tenant.headers)).text
    body = (await client.post(ENDPOINT, files=upload(), headers=tenant.headers)).json()
    for value in (body["data"]["total"], body["data"]["tax"]["cgst"]):
        assert isinstance(value, (int, float))
    # Whole rupees are emitted as integers, matching the printed document:
    # 118000, not 118000.0.
    assert '"total":118000' in raw.replace(" ", "")
    assert '"quantity":1' in raw.replace(" ", "")


async def test_multi_page_pdf_is_sent_as_multiple_pages(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    content = build_invoice_pdf(InvoiceSpec(extra_pages=2))
    response = await client.post(ENDPOINT, files=upload(content), headers=tenant.headers)
    assert response.status_code == 200
    assert response.json()["processing"]["pages"] == 3
    assert stub_provider.calls[0]["pages"] == 3


async def test_images_are_accepted(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    response = await client.post(
        ENDPOINT, files=upload(build_png(), "invoice.png", "image/png"), headers=tenant.headers
    )
    assert response.status_code == 200


async def test_interstate_invoice_validates_as_igst(
    client: httpx.AsyncClient, tenant: Tenant, use_provider
) -> None:
    use_provider(StubProvider(interstate_provider_output()))
    body = (await client.post(ENDPOINT, files=upload(), headers=tenant.headers)).json()
    assert body["data"]["tax"]["igst"] == 18000
    assert body["data"]["tax"]["cgst"] is None
    check = next(
        c for c in body["validation"]["checks"] if c["name"] == "supply_type_consistency"
    )
    assert check["status"] == "passed"


# --- honesty about what was not found ----------------------------------


async def test_absent_fields_are_null_never_invented(
    client: httpx.AsyncClient, tenant: Tenant, use_provider
) -> None:
    use_provider(StubProvider(sparse_provider_output()))
    body = (await client.post(ENDPOINT, files=upload(), headers=tenant.headers)).json()
    data = body["data"]
    assert data["buyer"]["name"] is None
    assert data["buyer"]["gstin"] is None
    assert data["due_date"] is None
    assert data["bank_details"] is None
    assert data["items"] == []
    # And the validation report says the buyer GSTIN was not checked, rather
    # than claiming it was valid.
    check = next(c for c in body["validation"]["checks"] if c["name"] == "buyer_gstin_format")
    assert check["status"] == "not_checked"
    assert body["validation"]["gstin_format_valid"] is True  # supplier alone passed


async def test_an_inconsistent_invoice_is_returned_with_failures_not_an_error(
    client: httpx.AsyncClient, tenant: Tenant, use_provider
) -> None:
    """A bad invoice is a successful extraction of a bad invoice."""
    use_provider(StubProvider(inconsistent_provider_output()))
    response = await client.post(ENDPOINT, files=upload(), headers=tenant.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["validation"]["overall"] == "failed"
    assert body["validation"]["calculation_matches"] is False
    assert body["validation"]["gstin_format_valid"] is False
    assert body["data"]["total"] == 125000  # reported as printed, not corrected


async def test_low_confidence_fields_are_listed(
    client: httpx.AsyncClient, tenant: Tenant, use_provider
) -> None:
    use_provider(StubProvider(inconsistent_provider_output()))
    body = (await client.post(ENDPOINT, files=upload(), headers=tenant.headers)).json()
    assert "buyer.gstin" in body["confidence"]["low_confidence_fields"]
    assert body["confidence"]["fields"]["buyer.gstin"]["band"] == "low"


# --- refusing to fake it (§42) -----------------------------------------


async def test_unconfigured_provider_returns_503_and_no_data(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    # No provider is installed, and the test environment has no credentials.
    response = await client.post(ENDPOINT, files=upload(), headers=tenant.headers)
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "extraction_provider_unavailable"
    assert "data" not in body


async def test_provider_failure_surfaces_as_422_not_fabricated_data(
    client: httpx.AsyncClient, tenant: Tenant, use_provider
) -> None:
    use_provider(StubProvider(raises=ExtractionFailedError("model returned prose")))
    response = await client.post(ENDPOINT, files=upload(), headers=tenant.headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "extraction_failed"


async def test_provider_outage_surfaces_as_503(
    client: httpx.AsyncClient, tenant: Tenant, use_provider
) -> None:
    use_provider(StubProvider(raises=ProviderUnavailableError()))
    response = await client.post(ENDPOINT, files=upload(), headers=tenant.headers)
    assert response.status_code == 503


async def test_an_unexpected_error_does_not_leak_internals(
    client: httpx.AsyncClient, tenant: Tenant, use_provider
) -> None:
    use_provider(StubProvider(raises=RuntimeError("psycopg: password=hunter2 at 10.0.0.5")))
    response = await client.post(ENDPOINT, files=upload(), headers=tenant.headers)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "hunter2" not in response.text
    assert "Traceback" not in response.text


# --- request rejection -------------------------------------------------


@pytest.mark.parametrize(
    "content,name,mime,expected_status,expected_code",
    [
        (b"MZ\x90\x00" + b"\x00" * 100, "x.pdf", "application/pdf", 415, "unsupported_file_type"),
        (b"%PDF-1.4 broken", "x.pdf", "application/pdf", 400, "invalid_file"),
        (b"", "x.pdf", "application/pdf", 400, "invalid_file"),
    ],
)
async def test_bad_uploads_are_rejected_before_the_provider(
    client: httpx.AsyncClient,
    tenant: Tenant,
    use_provider,
    stub_provider: StubProvider,
    content: bytes,
    name: str,
    mime: str,
    expected_status: int,
    expected_code: str,
) -> None:
    use_provider(stub_provider)
    response = await client.post(
        ENDPOINT, files={"file": (name, content, mime)}, headers=tenant.headers
    )
    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code
    assert stub_provider.calls == []  # no money was spent


async def test_oversized_upload_is_rejected_with_413(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    response = await client.post(
        ENDPOINT,
        files={"file": ("big.pdf", b"%PDF-1.4" + b"\x00" * (6 * 1024 * 1024), "application/pdf")},
        headers=tenant.headers,
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"


async def test_too_many_pages_is_rejected(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    response = await client.post(
        ENDPOINT, files=upload(build_invoice_pdf(InvoiceSpec(extra_pages=15))),
        headers=tenant.headers,
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "too_many_pages"


async def test_missing_file_field_is_a_400(client: httpx.AsyncClient, tenant: Tenant) -> None:
    response = await client.post(ENDPOINT, headers=tenant.headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


# --- persistence and usage ---------------------------------------------


async def test_document_extraction_and_validation_rows_are_written(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    body = (await client.post(ENDPOINT, files=upload(), headers=tenant.headers)).json()

    async with get_session_factory()() as session:
        document = (await session.execute(select(Document))).scalar_one()
        extraction = (await session.execute(select(Extraction))).scalar_one()
        validation = (await session.execute(select(ValidationResult))).scalar_one()

    assert document.id == body["document_id"]
    assert document.organization_id == tenant.organization_id
    assert document.status == "completed"
    assert document.request_id == body["request_id"]
    assert extraction.status == "succeeded"
    assert extraction.data["invoice_number"] == "INV-29381"
    assert extraction.overall_confidence is not None
    assert validation.overall == "passed"


async def test_usage_is_recorded_for_a_successful_request(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    await client.post(ENDPOINT, files=upload(), headers=tenant.headers)
    async with get_session_factory()() as session:
        event = (await session.execute(select(UsageEvent))).scalar_one()
    assert event.organization_id == tenant.organization_id
    assert event.api_key_id == tenant.api_key_id
    assert event.endpoint == ENDPOINT
    assert event.success is True
    assert event.billable is True
    assert event.pages == 1
    assert event.input_tokens == 1200
    assert event.duration_ms is not None


async def test_usage_is_recorded_when_extraction_fails(
    client: httpx.AsyncClient, tenant: Tenant, use_provider
) -> None:
    """A failed call still cost us a provider request, so it still counts."""
    use_provider(StubProvider(raises=ExtractionFailedError()))
    await client.post(ENDPOINT, files=upload(), headers=tenant.headers)
    async with get_session_factory()() as session:
        event = (await session.execute(select(UsageEvent))).scalar_one()
        extraction = (await session.execute(select(Extraction))).scalar_one()
    assert event.success is False
    assert event.billable is True
    assert event.error_code == "extraction_failed"
    assert extraction.status == "failed"
    assert extraction.error_code == "extraction_failed"


async def test_a_rejected_file_does_not_consume_quota(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    await client.post(
        ENDPOINT,
        files={"file": ("x.gif", b"GIF89a" + b"\x00" * 50, "image/gif")},
        headers=tenant.headers,
    )
    async with get_session_factory()() as session:
        events = list((await session.execute(select(UsageEvent))).scalars())
    assert all(not event.billable for event in events)


async def test_the_uploaded_bytes_are_stored(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider,
    object_store,
) -> None:
    use_provider(stub_provider)
    content = build_invoice_pdf()
    body = (
        await client.post(ENDPOINT, files=upload(content), headers=tenant.headers)
    ).json()
    async with get_session_factory()() as session:
        document = (await session.execute(select(Document))).scalar_one()
    assert document.storage_key is not None
    assert document.storage_key.startswith(f"{tenant.organization_id}/")
    assert object_store.objects[document.storage_key] == content
    assert document.retention_expires_at is not None
    assert body["document_id"] == document.id
