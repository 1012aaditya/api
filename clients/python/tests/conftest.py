"""Test helpers.

Every test drives the real client through ``httpx.MockTransport``, so the
request the library actually builds — headers, multipart parts, query string —
is the thing under test. Nothing here stubs out the client's own code.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

import httpx
import pytest

from docuparse import DocuParse


class Recorder:
    """Captures requests and replays a scripted list of responses."""

    def __init__(self, responses: List[httpx.Response]) -> None:
        self._responses = list(responses)
        self.requests: List[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError(
                f"Unexpected extra request: {request.method} {request.url}"
            )
        return self._responses.pop(0)

    @property
    def calls(self) -> int:
        return len(self.requests)


def ok(payload: Dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def raw(body: bytes, status: int = 200, **kwargs: Any) -> httpx.Response:
    return httpx.Response(status, content=body, **kwargs)


def api_error(
    status: int,
    code: str,
    message: str = "nope",
    request_id: str = "req_test",
    **extra: Any,
) -> httpx.Response:
    error: Dict[str, Any] = {"code": code, "message": message}
    error.update(extra)
    return httpx.Response(
        status,
        json={"success": False, "request_id": request_id, "error": error},
    )


@pytest.fixture
def make_client() -> Callable[..., Any]:
    def factory(responses: List[httpx.Response], **kwargs: Any):
        recorder = Recorder(responses)
        http_client = httpx.Client(transport=httpx.MockTransport(recorder))
        client = DocuParse(
            api_key="dp_live_testkey0000000000000000000000",
            base_url="https://api.example.test",
            http_client=http_client,
            **kwargs,
        )
        return client, recorder

    return factory


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry backoff must not make the suite slow or flaky."""
    import docuparse._http as http_module

    monkeypatch.setattr(http_module.time, "sleep", lambda _seconds: None)


def multipart_filenames(request: httpx.Request) -> List[str]:
    """The filenames the client actually put on the wire."""
    body = request.content.decode("utf-8", "replace")
    return [
        chunk.split('filename="', 1)[1].split('"', 1)[0]
        for chunk in body.split("Content-Disposition")
        if 'filename="' in chunk
    ]


def json_body(request: httpx.Request) -> Any:
    return json.loads(request.content.decode())
