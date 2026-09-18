"""Bulk upload and CSV export."""

from __future__ import annotations

import csv
import io

import pytest
from conftest import api_error, multipart_filenames, ok, raw

from docuparse import InvalidRequest, ProviderUnavailable, QuotaExceeded

SUBMISSION = {
    "success": True,
    "request_id": "req_b1",
    "batch_id": "bat_1",
    "accepted": 2,
    "job_ids": ["job_1", "job_2"],
    "rejected": [
        {
            "filename": "notes.gif",
            "code": "unsupported_file_type",
            "message": "Only PDF, PNG, JPG and JPEG files are supported.",
        }
    ],
}


def _pdfs(tmp_path, count=3):
    paths = []
    for index in range(count):
        path = tmp_path / f"inv-{index:02d}.pdf"
        path.write_bytes(b"%PDF-1.4 fake")
        paths.append(path)
    return paths


def test_create_a_batch_from_a_list_of_paths(make_client, tmp_path):
    client, recorder = make_client([ok(SUBMISSION)])
    receipt = client.batches.create(_pdfs(tmp_path), name="September purchases")

    assert receipt.batch_id == "bat_1"
    assert receipt.accepted == 2
    assert receipt.job_ids == ["job_1", "job_2"]

    request = recorder.requests[0]
    assert request.url.path == "/v1/batches"
    assert multipart_filenames(request) == ["inv-00.pdf", "inv-01.pdf", "inv-02.pdf"]
    assert b"September purchases" in request.content


def test_rejections_come_back_named_with_a_reason(make_client, tmp_path):
    """The point of the endpoint: a bad file is reported, not dropped."""
    client, _ = make_client([ok(SUBMISSION)])
    receipt = client.batches.create(_pdfs(tmp_path, 1))

    assert len(receipt.rejected) == 1
    rejected = receipt.rejected[0]
    assert rejected.filename == "notes.gif"
    assert rejected.code == "unsupported_file_type"
    assert "PDF" in rejected.message


def test_a_directory_is_scanned_sorted_and_filtered(make_client, tmp_path):
    _pdfs(tmp_path, 3)
    (tmp_path / "notes.txt").write_text("ignore me")
    (tmp_path / "scan.PNG").write_bytes(b"\x89PNG")
    (tmp_path / "subdir").mkdir()

    client, recorder = make_client([ok(SUBMISSION)])
    client.batches.create(tmp_path)

    assert multipart_filenames(recorder.requests[0]) == [
        "inv-00.pdf",
        "inv-01.pdf",
        "inv-02.pdf",
        "scan.PNG",
    ]


def test_an_empty_directory_fails_before_the_round_trip(make_client, tmp_path):
    client, recorder = make_client([])
    with pytest.raises(ValueError, match="No PDF"):
        client.batches.create(tmp_path)
    assert recorder.calls == 0


def test_batch_progress_and_wait(make_client):
    running = {
        "success": True,
        "data": {
            "id": "bat_1",
            "name": "Sept",
            "document_count": 3,
            "rejected_count": 1,
            "total": 3,
            "queued": 2,
            "processing": 1,
            "completed": 0,
            "failed": 0,
            "done": False,
            "created_at": "2026-09-18T10:00:00Z",
        },
    }
    finished = {
        "success": True,
        "data": dict(running["data"], queued=0, processing=0, completed=3, done=True),
    }

    client, recorder = make_client([ok(running), ok(finished)])
    batch = client.batches.wait("bat_1", poll_interval=0)

    assert batch.done is True
    assert batch.completed == 3
    assert batch.failed == 0
    assert recorder.calls == 2


def test_batch_wait_times_out_and_says_what_is_outstanding(make_client):
    running = {
        "success": True,
        "data": {
            "id": "bat_1",
            "total": 5,
            "queued": 4,
            "processing": 1,
            "completed": 0,
            "failed": 0,
            "done": False,
        },
    }
    client, _ = make_client([ok(running)])
    with pytest.raises(TimeoutError, match="5 file"):
        client.batches.wait("bat_1", timeout=0.0, poll_interval=0)


def test_quota_exhaustion_raises_on_the_whole_batch(make_client, tmp_path):
    client, _ = make_client(
        [api_error(403, "quota_exceeded", "Monthly quota used.")], max_retries=0
    )
    with pytest.raises(QuotaExceeded):
        client.batches.create(_pdfs(tmp_path, 1))


# -- export -------------------------------------------------------------

CSV_BYTES = (
    "﻿".encode()
    + b"invoice_number,supplier_name,total,igst\r\n"
    + 'INV-1,"Acme Traders, Pune",118000.60,\r\n'.encode()
)


def test_export_streams_to_a_file(make_client, tmp_path):
    client, recorder = make_client([raw(CSV_BYTES)])
    target = tmp_path / "out" / "september.csv"

    written = client.exports.invoices(target, start="2026-09-01", end="2026-09-30")

    assert written == target
    assert target.read_bytes() == CSV_BYTES
    query = dict(recorder.requests[0].url.params)
    assert query == {"from": "2026-09-01", "to": "2026-09-30"}


def test_export_accepts_date_objects_and_a_batch_filter(make_client):
    import datetime as dt

    client, recorder = make_client([raw(CSV_BYTES)])
    client.exports.line_items(start=dt.date(2026, 9, 1), batch_id="bat_1")

    request = recorder.requests[0]
    assert request.url.path == "/v1/exports/line-items.csv"
    assert dict(request.url.params) == {"from": "2026-09-01", "batch_id": "bat_1"}


def test_export_without_a_destination_returns_bytes(make_client):
    client, _ = make_client([raw(CSV_BYTES)])
    body = client.exports.invoices()
    assert isinstance(body, bytes)
    assert body.startswith(b"\xef\xbb\xbf")  # the BOM Excel needs


def test_exported_csv_round_trips_through_the_csv_module(make_client):
    client, _ = make_client([raw(CSV_BYTES)])
    body = client.exports.invoices()

    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    assert rows[0]["invoice_number"] == "INV-1"
    assert rows[0]["supplier_name"] == "Acme Traders, Pune"  # comma survived quoting
    assert rows[0]["igst"] == ""  # a null is an empty cell, never "None"


def test_an_export_error_is_raised_not_written_to_the_file(make_client, tmp_path):
    client, _ = make_client(
        [api_error(400, "invalid_request", "Export windows are limited to 400 days.")],
        max_retries=0,
    )
    target = tmp_path / "out.csv"

    with pytest.raises(InvalidRequest) as caught:
        client.exports.invoices(target)

    assert "400 days" in str(caught.value)
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []  # not even a stray partial


def test_a_failed_export_does_not_clobber_the_previous_one(make_client, tmp_path):
    """Overwriting a good export with an empty file would be the worst outcome."""
    target = tmp_path / "september.csv"
    target.write_bytes(CSV_BYTES)

    client, _ = make_client(
        [api_error(503, "extraction_provider_unavailable")], max_retries=0
    )
    with pytest.raises(ProviderUnavailable):
        client.exports.invoices(target)

    assert target.read_bytes() == CSV_BYTES
    assert list(tmp_path.iterdir()) == [target]
