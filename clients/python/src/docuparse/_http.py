"""The transport: one place that knows about auth, retries and errors.

The retry policy is the interesting part, and it is deliberately asymmetric.

A ``GET`` can be repeated freely, so a network blip or a 5xx is retried. A
``POST`` that submits a document cannot: the server may well have processed it
before the connection died, and a blind retry would extract — and bill, and
count against the quota — the same invoice twice. So a ``POST`` is retried only
on ``429``, which is the one status that proves the request was *not* processed.

The alternative would be idempotency keys, which the API does not offer yet.
Until it does, this library would rather return an error the caller can decide
about than quietly double-charge them.
"""

from __future__ import annotations

import json
import random
import time
from decimal import Decimal
from typing import Any, Dict, Iterator, Optional

import httpx

from .errors import APIConnectionError, DocuParseError, error_from_response

USER_AGENT = "docuparse-python/0.1.0"

_IDEMPOTENT = frozenset({"GET", "HEAD", "DELETE"})
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def _parse_body(text: str) -> Optional[Dict[str, Any]]:
    """Parse JSON with exact decimals.

    ``parse_float=Decimal`` is the whole reason this is not ``response.json()``:
    it keeps ``118000.50`` from becoming ``118000.49999999999`` on the way in.
    """
    if not text:
        return None
    try:
        parsed = json.loads(text, parse_float=Decimal)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def _retry_after(response: httpx.Response) -> Optional[float]:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


class Transport:
    """Auth, retries, and the error contract. Not part of the public API."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout: float = 30.0,
        max_retries: int = 2,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_retries = max(0, max_retries)
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        )

    # -- lifecycle ------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- internals ------------------------------------------------------

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _sleep_for(self, attempt: int, retry_after: Optional[float]) -> float:
        if retry_after is not None:
            # The server knows better than our backoff curve does.
            return min(retry_after, 60.0)
        # Full jitter: spreads a thundering herd instead of synchronising it.
        return random.uniform(0, min(0.5 * (2**attempt), 8.0))

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        files: Optional[Any] = None,
        data: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Perform a request and return the decoded body, or raise."""
        method = method.upper()
        retryable = method in _IDEMPOTENT
        last_error: Optional[DocuParseError] = None

        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.request(
                    method,
                    self._url(path),
                    params=params,
                    files=files,
                    data=data,
                    json=json_body,
                    headers=self._headers,
                    timeout=timeout if timeout is not None else self._client.timeout,
                )
            except httpx.HTTPError as exc:
                last_error = APIConnectionError(
                    f"Could not reach the DocuParse API: {exc}", code="connection_error"
                )
                # A POST may have been processed before the connection broke.
                # Retrying it could extract and bill the same document twice.
                if not retryable or attempt == self.max_retries:
                    raise last_error from exc
                time.sleep(self._sleep_for(attempt, None))
                continue

            if response.status_code < 400:
                return _parse_body(response.text) or {}

            error = error_from_response(
                response.status_code,
                _parse_body(response.text),
                retry_after=_retry_after(response),
            )

            # 429 is the one status that proves the request did not run, so it
            # is safe to repeat even for a POST.
            may_retry = response.status_code in _RETRY_STATUSES and (
                retryable or response.status_code == 429
            )
            if not may_retry or attempt == self.max_retries:
                raise error
            last_error = error
            time.sleep(self._sleep_for(attempt, _retry_after(response)))

        raise last_error or DocuParseError("Request failed.", code="internal_error")

    def stream_to(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Iterator[bytes]:
        """Stream a response body in chunks, raising on an error status.

        Used for CSV export, where the point is not to hold the whole file in
        memory just to write it straight back out to disk.
        """
        with self._client.stream(
            "GET",
            self._url(path),
            params=params,
            headers=self._headers,
            timeout=timeout if timeout is not None else self._client.timeout,
        ) as response:
            if response.status_code >= 400:
                response.read()
                raise error_from_response(
                    response.status_code,
                    _parse_body(response.text),
                    retry_after=_retry_after(response),
                )
            for chunk in response.iter_bytes():
                yield chunk
