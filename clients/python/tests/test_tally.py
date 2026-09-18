"""The Tally namespace."""

from __future__ import annotations

import datetime as dt

import pytest
from conftest import api_error, multipart_filenames, ok, raw

from docuparse import InvalidRequest

PREVIEW = {
    "success": True,
    "data": {
        "postable": 12,
        "blocked": 3,
        "ledger_count": 42,
        "settings_configured": True,
        "unmatched_suppliers": [
            {
                "name": "Acme Traders",
                "gstin": "27AAACA1111A1Z5",
                "documents": 3,
                "suggestions": [
                    {
                        "ledger_id": "led_1",
                        "ledger_name": "Acme Traders - Pune",
                        "score": 0.85,
                    }
                ],
            }
        ],
        "vouchers": [],
    },
}

XML = b'<?xml version="1.0" encoding="utf-8"?>\n<ENVELOPE><BODY/></ENVELOPE>\n'


def test_importing_a_master_sends_the_file(make_client, tmp_path):
    master = tmp_path / "master.xml"
    master.write_bytes(b"<ENVELOPE/>")
    client, recorder = make_client(
        [
            ok(
                {
                    "success": True,
                    "data": {"imported": 42, "replaced": 40, "aliases_kept": 7},
                }
            )
        ]
    )

    result = client.tally.import_ledgers(master)

    assert result["imported"] == 42
    assert result["aliases_kept"] == 7
    request = recorder.requests[0]
    assert request.url.path == "/v1/tally/ledgers"
    assert multipart_filenames(request) == ["master.xml"]


def test_configure_sends_ledger_names_and_reports_unknown_ones(make_client):
    client, recorder = make_client(
        [
            ok(
                {
                    "success": True,
                    "data": {
                        "purchase_ledger": "Purchase 18%",
                        "configured": True,
                        "unknown_ledgers": ["Input IGST"],
                    },
                }
            )
        ]
    )
    result = client.tally.configure(
        purchase_ledger="Purchase 18%", igst_ledger="Input IGST"
    )

    assert result["configured"] is True
    assert result["unknown_ledgers"] == ["Input IGST"]
    assert recorder.requests[0].method == "PUT"


def test_preview_passes_the_window_through(make_client):
    client, recorder = make_client([ok(PREVIEW)])
    result = client.tally.preview(
        start=dt.date(2026, 9, 1), end="2026-09-30", batch_id="bat_1"
    )

    assert result["postable"] == 12
    assert result["unmatched_suppliers"][0]["suggestions"][0]["ledger_name"] == (
        "Acme Traders - Pune"
    )
    params = dict(recorder.requests[0].url.params)
    assert params == {
        "limit": "200",
        "from": "2026-09-01",
        "to": "2026-09-30",
        "batch_id": "bat_1",
    }


def test_confirming_a_match_stores_both_keys(make_client):
    client, recorder = make_client(
        [
            ok(
                {
                    "success": True,
                    "data": [
                        {
                            "id": "lal_1",
                            "ledger_id": "led_1",
                            "key_type": "gstin",
                            "match_key": "27AAACA1111A1Z5",
                            "created_at": "2026-09-18T10:00:00Z",
                        },
                        {
                            "id": "lal_2",
                            "ledger_id": "led_1",
                            "key_type": "name",
                            "match_key": "acme traders",
                            "created_at": "2026-09-18T10:00:00Z",
                        },
                    ],
                },
                status=201,
            )
        ]
    )
    written = client.tally.confirm_match(
        "led_1", supplier_name="Acme Traders", supplier_gstin="27AAACA1111A1Z5"
    )
    assert {row["key_type"] for row in written} == {"gstin", "name"}


def test_vouchers_stream_to_a_file(make_client, tmp_path):
    client, _ = make_client([raw(XML)])
    target = tmp_path / "out" / "tally.xml"

    written = client.tally.vouchers(target, batch_id="bat_1")

    assert written == target
    assert target.read_bytes() == XML


def test_nothing_ready_raises_rather_than_writing_an_empty_file(make_client, tmp_path):
    """An empty envelope imports cleanly into Tally and does nothing, silently."""
    client, _ = make_client(
        [
            api_error(
                400, "invalid_request", "Nothing is ready to post. Check the preview"
            )
        ],
        max_retries=0,
    )
    target = tmp_path / "tally.xml"

    with pytest.raises(InvalidRequest, match="preview"):
        client.tally.vouchers(target)

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_a_failed_download_does_not_clobber_last_months_file(make_client, tmp_path):
    target = tmp_path / "tally.xml"
    target.write_bytes(XML)
    client, _ = make_client([api_error(400, "invalid_request")], max_retries=0)

    with pytest.raises(InvalidRequest):
        client.tally.vouchers(target)

    assert target.read_bytes() == XML
