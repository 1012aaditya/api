"""The client's behaviour against a scripted API."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from conftest import multipart_filenames, ok, raw

from docuparse.models import Extraction

EXTRACTION = {
    "success": True,
    "request_id": "req_01",
    "document_id": "doc_01",
    "extraction_id": "ext_01",
    "data": {
        "document_type": "gst_invoice",
        "invoice_number": "INV-2025-0042",
        "invoice_date": "2025-09-14",
        "due_date": None,
        "place_of_supply": "27-Maharashtra",
        "supplier": {"name": "Acme Traders", "gstin": "27AAACA1111A1Z5", "pan": None},
        "buyer": {"name": "Sharma & Associates", "gstin": "27AAACB2222B1Z3"},
        "items": [
            {
                "description": "Consulting",
                "hsn_sac": "998311",
                "quantity": 2,
                "unit_price": 50000.25,
                "taxable_value": 100000.50,
                "cgst": 9000.05,
                "sgst": 9000.05,
                "igst": None,
                "total": 118000.60,
            }
        ],
        "subtotal": 100000.50,
        "total": 118000.60,
        "currency": "INR",
        "tax": {
            "taxable_amount": 100000.50,
            "cgst": 9000.05,
            "sgst": 9000.05,
            "igst": None,
        },
    },
    "confidence": {
        "overall": 0.862,
        "band": "high",
        "low_confidence_fields": ["buyer.pan"],
        "fields": {
            "invoice_number": {
                "confidence": 0.94,
                "band": "high",
                "page": 1,
                "source_text": "INV-2025-0042",
            },
            "buyer.pan": {
                "confidence": 0.41,
                "band": "low",
                "page": None,
                "source_text": None,
            },
        },
    },
    "validation": {
        "overall": "passed",
        "checks": [
            {"name": "gstin_format", "status": "passed", "message": None},
            {"name": "line_item_sum", "status": "warning", "message": "Off by 0.05"},
            {
                "name": "irn_present",
                "status": "not_checked",
                "message": "No IRN on the document.",
            },
        ],
        "gstin_format_valid": True,
        "calculation_matches": True,
        "required_fields_present": True,
    },
    "processing": {
        "duration_ms": 157,
        "pages": 1,
        "provider": "openai_compatible",
        "model": "some-vision-model",
        "prompt_version": "v1",
        "tiers": ["qr", "text_layer"],
        "model_called": False,
        "escalation_reason": None,
        "notes": ["Text layer answered every required field."],
        "estimated_cost_usd": None,
    },
}


def test_extract_parses_the_invoice(make_client, tmp_path):
    invoice = tmp_path / "invoice.pdf"
    invoice.write_bytes(b"%PDF-1.4 fake")
    client, recorder = make_client([ok(EXTRACTION)])

    result = client.extract(invoice)

    assert isinstance(result, Extraction)
    assert result.data.invoice_number == "INV-2025-0042"
    assert result.data.invoice_date == dt.date(2025, 9, 14)
    assert result.data.supplier.name == "Acme Traders"
    assert result.data.items[0].description == "Consulting"
    assert result.request_id == "req_01"

    request = recorder.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/invoices/extract"
    assert request.headers["authorization"].startswith("Bearer dp_live_")
    assert multipart_filenames(request) == ["invoice.pdf"]


def test_amounts_are_exact_decimals_not_floats(make_client):
    """The whole reason the transport parses the JSON itself.

    The body is sent as raw text so the assertion is about the client, not
    about how the test fixture happened to serialise a Python float.
    """
    body = (
        b'{"success":true,"request_id":"r","document_id":"d","extraction_id":"e",'
        b'"data":{"total":118000.123456789012345678,'
        b'"tax":{"cgst":0.1,"sgst":0.2}},'
        b'"confidence":{},"validation":{"overall":"passed"},"processing":{}}'
    )
    client, _ = make_client([raw(body, headers={"content-type": "application/json"})])
    result = client.extract(("x.pdf", b"%PDF"))

    total = result.data.total
    assert isinstance(total, Decimal)
    # A float would have silently truncated this to 118000.12345678901.
    assert total == Decimal("118000.123456789012345678")

    # And the arithmetic stays exact, which floats famously do not.
    assert result.data.tax.cgst + result.data.tax.sgst == Decimal("0.3")
    assert 0.1 + 0.2 != 0.3  # the bug this avoids


def test_integer_amounts_are_decimals_too(make_client):
    body = (
        b'{"success":true,"request_id":"r","document_id":"d","extraction_id":"e",'
        b'"data":{"total":118000},"confidence":{},'
        b'"validation":{"overall":"passed"},"processing":{}}'
    )
    client, _ = make_client([raw(body, headers={"content-type": "application/json"})])
    total = client.extract(("x.pdf", b"%PDF")).data.total
    assert isinstance(total, Decimal)
    assert total == Decimal(118000)


def test_absent_fields_stay_none(make_client):
    client, _ = make_client([ok(EXTRACTION)])
    result = client.extract(("x.pdf", b"%PDF"))

    assert result.data.due_date is None
    assert result.data.supplier.pan is None
    assert result.data.items[0].igst is None
    assert result.data.tax.igst is None
    # Absent must not be coerced to zero: a zero would post a wrong number
    # to a ledger, and "not on the invoice" is not "no tax was charged".
    assert result.data.items[0].igst != Decimal(0)


def test_tax_breakdown_knows_interstate_from_the_data(make_client):
    client, _ = make_client([ok(EXTRACTION)])
    result = client.extract(("x.pdf", b"%PDF"))
    assert result.data.tax.is_interstate is False


def test_interstate_is_none_when_nothing_was_read():
    from docuparse.models import TaxBreakdown

    assert TaxBreakdown.from_json({}).is_interstate is None


def test_validation_helpers(make_client):
    client, _ = make_client([ok(EXTRACTION)])
    result = client.extract(("x.pdf", b"%PDF"))

    assert result.validation.passed is True
    assert [c.name for c in result.validation.warnings()] == ["line_item_sum"]
    assert result.validation.failures() == []
    # not_checked is not a pass, and must not be reported as one.
    statuses = {c.name: c.status for c in result.validation.checks}
    assert statuses["irn_present"] == "not_checked"


def test_needs_review_flags_low_confidence_even_when_validation_passed(make_client):
    client, _ = make_client([ok(EXTRACTION)])
    result = client.extract(("x.pdf", b"%PDF"))

    assert result.validation.passed is True
    assert result.confidence.needs_review() is True
    assert result.needs_review() is True
    assert result.confidence.field_confidence("buyer.pan").band == "low"
    assert result.confidence.field_confidence("nope") is None


def test_processing_reports_no_model_call(make_client):
    client, _ = make_client([ok(EXTRACTION)])
    result = client.extract(("x.pdf", b"%PDF"))
    assert result.processing.model_called is False
    assert result.processing.tiers == ["qr", "text_layer"]


def test_raw_keeps_fields_the_sdk_does_not_know_about(make_client):
    payload = dict(EXTRACTION)
    payload["data"] = dict(EXTRACTION["data"], a_new_server_field="hello")
    client, _ = make_client([ok(payload)])

    result = client.extract(("x.pdf", b"%PDF"))
    assert result.data.raw["a_new_server_field"] == "hello"


def test_stored_extraction_normalises_to_the_same_type(make_client):
    stored = {
        "success": True,
        "request_id": "req_09",
        "data": {
            "id": "ext_77",
            "document_id": "doc_77",
            "request_id": "req_original",
            "status": "completed",
            "document_type": "gst_invoice",
            "data": EXTRACTION["data"],
            "confidence": EXTRACTION["confidence"]["fields"],
            "overall_confidence": 0.862,
            "validation": {
                "overall": "passed",
                "checks": EXTRACTION["validation"]["checks"],
            },
            "provider": "openai_compatible",
            "model": "some-vision-model",
            "total_latency_ms": 157,
            "estimated_cost_usd": None,
        },
    }
    client, _ = make_client([ok(stored)])
    result = client.documents.extraction("doc_77")

    assert result.extraction_id == "ext_77"
    assert result.data.invoice_number == "INV-2025-0042"
    assert result.confidence.overall == Decimal("0.862")
    # Derived from each field's band, because the stored shape omits the list.
    assert result.confidence.low_confidence_fields == ["buyer.pan"]
    assert result.validation.passed is True
    # Not stored, so unknown — and unknown is not False.
    assert result.validation.gstin_format_valid is None
    assert result.processing.duration_ms == 157


def test_submit_and_wait_for_a_job(make_client):
    client, recorder = make_client(
        [
            ok(
                {
                    "success": True,
                    "request_id": "r",
                    "job_id": "job_1",
                    "document_id": "doc_1",
                    "status": "queued",
                }
            ),
            ok(
                {
                    "success": True,
                    "data": {
                        "id": "job_1",
                        "status": "processing",
                        "document_id": "doc_1",
                        "attempts": 1,
                        "max_attempts": 3,
                        "created_at": "2026-09-18T10:00:00Z",
                        "started_at": None,
                        "completed_at": None,
                        "request_id": None,
                    },
                }
            ),
            ok(
                {
                    "success": True,
                    "data": {
                        "id": "job_1",
                        "status": "completed",
                        "document_id": "doc_1",
                        "extraction_id": "ext_1",
                        "attempts": 1,
                        "max_attempts": 3,
                        "created_at": "2026-09-18T10:00:00Z",
                        "started_at": None,
                        "completed_at": "2026-09-18T10:00:05Z",
                        "request_id": None,
                    },
                }
            ),
        ]
    )

    job = client.documents.submit(("a.pdf", b"%PDF"))
    assert job.id == "job_1"
    assert job.done is False

    finished = client.jobs.wait("job_1", poll_interval=0)
    assert finished.succeeded is True
    assert finished.extraction_id == "ext_1"
    assert finished.completed_at.year == 2026
    assert recorder.calls == 3


def test_a_failed_job_is_returned_not_raised(make_client):
    client, _ = make_client(
        [
            ok(
                {
                    "success": True,
                    "data": {
                        "id": "job_2",
                        "status": "failed",
                        "document_id": "doc_2",
                        "attempts": 3,
                        "max_attempts": 3,
                        "error": {"code": "extraction_failed", "message": "Unreadable."},
                        "created_at": "2026-09-18T10:00:00Z",
                        "started_at": None,
                        "completed_at": None,
                        "request_id": None,
                    },
                }
            )
        ]
    )
    job = client.jobs.wait("job_2", poll_interval=0)
    assert job.done is True
    assert job.succeeded is False
    assert job.error["code"] == "extraction_failed"


def test_job_wait_times_out_without_pretending_the_job_stopped(make_client):
    client, _ = make_client(
        [
            ok(
                {
                    "success": True,
                    "data": {
                        "id": "job_3",
                        "status": "queued",
                        "document_id": "d",
                        "attempts": 0,
                        "max_attempts": 3,
                        "created_at": "2026-09-18T10:00:00Z",
                        "started_at": None,
                        "completed_at": None,
                        "request_id": None,
                    },
                }
            )
        ]
    )
    with pytest.raises(TimeoutError, match="still running on the server"):
        client.jobs.wait("job_3", timeout=0.0, poll_interval=0)
