"""The error contract, and the deliberately asymmetric retry policy."""

from __future__ import annotations

import httpx
import pytest
from conftest import api_error, ok

from docuparse import (
    AuthenticationError,
    DocuParse,
    DocuParseError,
    ExtractionFailed,
    InvalidRequest,
    ProviderUnavailable,
    QuotaExceeded,
    RateLimited,
    ServerError,
    UnsupportedFile,
)
from docuparse.errors import APIConnectionError

JOB_OK = {
    "success": True,
    "data": {
        "id": "job_1",
        "status": "completed",
        "document_id": "d",
        "attempts": 1,
        "max_attempts": 3,
        "created_at": "2026-09-18T10:00:00Z",
        "started_at": None,
        "completed_at": None,
        "request_id": None,
    },
}


@pytest.mark.parametrize(
    "status,code,expected",
    [
        (400, "invalid_request", InvalidRequest),
        (401, "invalid_api_key", AuthenticationError),
        (403, "quota_exceeded", QuotaExceeded),
        (404, "not_found", InvalidRequest),
        (413, "file_too_large", UnsupportedFile),
        (415, "unsupported_file_type", UnsupportedFile),
        (422, "extraction_failed", ExtractionFailed),
        (429, "rate_limit_exceeded", RateLimited),
        (500, "internal_error", ServerError),
        (503, "extraction_provider_unavailable", ProviderUnavailable),
    ],
)
def test_status_maps_to_exception(make_client, status, code, expected):
    client, _ = make_client([api_error(status, code)], max_retries=0)
    with pytest.raises(expected) as caught:
        client.extract(("x.pdf", b"%PDF"))
    assert caught.value.code == code
    assert caught.value.status_code == status


def test_every_error_carries_the_request_id(make_client):
    """It is the only handle that finds one call in the server's logs."""
    client, _ = make_client(
        [api_error(422, "extraction_failed", request_id="req_abc")], max_retries=0
    )
    with pytest.raises(ExtractionFailed) as caught:
        client.extract(("x.pdf", b"%PDF"))
    assert caught.value.request_id == "req_abc"
    assert "req_abc" in str(caught.value)


def test_quota_is_catchable_as_permission_denied(make_client):
    from docuparse import PermissionDenied

    client, _ = make_client([api_error(403, "quota_exceeded")], max_retries=0)
    with pytest.raises(PermissionDenied):
        client.extract(("x.pdf", b"%PDF"))


def test_an_unknown_future_code_still_lands_on_the_right_class(make_client):
    client, _ = make_client(
        [api_error(403, "some_code_invented_next_year")], max_retries=0
    )
    with pytest.raises(DocuParseError) as caught:
        client.extract(("x.pdf", b"%PDF"))
    assert caught.value.code == "some_code_invented_next_year"


def test_rate_limit_exposes_retry_after(make_client):
    response = httpx.Response(
        429,
        json={
            "success": False,
            "request_id": "req_1",
            "error": {"code": "rate_limit_exceeded", "message": "slow down"},
        },
        headers={"Retry-After": "12"},
    )
    client, _ = make_client([response], max_retries=0)
    with pytest.raises(RateLimited) as caught:
        client.extract(("x.pdf", b"%PDF"))
    assert caught.value.retry_after == 12.0


def test_a_non_json_error_body_still_raises_cleanly(make_client):
    client, _ = make_client(
        [httpx.Response(502, content=b"<html>bad gateway</html>")], max_retries=0
    )
    with pytest.raises(ServerError) as caught:
        client.jobs.get("job_1")
    assert caught.value.status_code == 502


# -- retry policy -------------------------------------------------------


def test_get_retries_a_server_error(make_client):
    client, recorder = make_client(
        [api_error(500, "internal_error"), ok(JOB_OK)], max_retries=2
    )
    job = client.jobs.get("job_1")
    assert job.id == "job_1"
    assert recorder.calls == 2


def test_post_does_not_retry_a_server_error(make_client):
    """A retried POST could extract, bill and count the same invoice twice."""
    client, recorder = make_client([api_error(500, "internal_error")], max_retries=3)
    with pytest.raises(ServerError):
        client.extract(("x.pdf", b"%PDF"))
    assert recorder.calls == 1


def test_post_does_retry_a_rate_limit(make_client):
    """429 is the one status that proves the request was not processed."""
    extraction = {
        "success": True,
        "request_id": "r",
        "document_id": "d",
        "extraction_id": "e",
        "data": {},
        "confidence": {},
        "validation": {"overall": "passed"},
        "processing": {},
    }
    client, recorder = make_client(
        [api_error(429, "rate_limit_exceeded"), ok(extraction)], max_retries=2
    )
    result = client.extract(("x.pdf", b"%PDF"))
    assert result.document_id == "d"
    assert recorder.calls == 2


def test_retries_are_bounded(make_client):
    client, recorder = make_client([api_error(500, "internal_error")] * 3, max_retries=2)
    with pytest.raises(ServerError):
        client.jobs.get("job_1")
    assert recorder.calls == 3  # the first try plus two retries


def test_a_network_failure_on_a_post_is_not_retried():
    """Same reasoning: the server may have processed it before the wire died."""
    attempts = {"n": 0}

    def explode(_request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        raise httpx.ConnectError("connection refused")

    client = DocuParse(
        api_key="dp_live_x",
        base_url="https://api.example.test",
        http_client=httpx.Client(transport=httpx.MockTransport(explode)),
        max_retries=3,
    )
    with pytest.raises(APIConnectionError):
        client.extract(("x.pdf", b"%PDF"))
    assert attempts["n"] == 1


def test_a_network_failure_on_a_get_is_retried():
    attempts = {"n": 0}

    def flaky(_request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json=JOB_OK)

    client = DocuParse(
        api_key="dp_live_x",
        base_url="https://api.example.test",
        http_client=httpx.Client(transport=httpx.MockTransport(flaky)),
        max_retries=3,
    )
    assert client.jobs.get("job_1").id == "job_1"
    assert attempts["n"] == 3


# -- credentials --------------------------------------------------------


def test_missing_key_is_refused_up_front(monkeypatch):
    monkeypatch.delenv("DOCUPARSE_API_KEY", raising=False)
    with pytest.raises(DocuParseError, match="DOCUPARSE_API_KEY"):
        DocuParse()


def test_key_and_base_url_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("DOCUPARSE_API_KEY", "dp_live_from_env")
    monkeypatch.setenv("DOCUPARSE_BASE_URL", "https://docuparse.internal/")
    client = DocuParse()
    assert client.base_url == "https://docuparse.internal"


def test_repr_never_leaks_the_key(make_client):
    client, _ = make_client([])
    text = repr(client)
    assert "dp_live_testkey0000000000000000000000" not in text
    assert text.endswith("api_key='...0000')")
