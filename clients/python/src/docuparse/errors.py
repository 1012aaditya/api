"""Exceptions, mirroring the API's public error contract.

Every error carries the ``request_id``. Quote it in a support request — it is
the only handle that finds one specific call in the server's logs.

The class hierarchy is the contract; the ``code`` string is the detail. Catch
``QuotaExceeded`` if you want to react to running out of allowance, catch
``DocuParseError`` if you just want "the call failed". An unfamiliar code from
a newer server still arrives as the right *class* (chosen by HTTP status), so
upgrading the API does not break your ``except`` clauses.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class DocuParseError(Exception):
    """Base class for everything this library raises."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "error",
        status_code: Optional[int] = None,
        request_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.request_id = request_id
        self.details = details or {}

    def __str__(self) -> str:
        suffix = f" (request_id={self.request_id})" if self.request_id else ""
        return f"[{self.code}] {self.message}{suffix}"


class APIConnectionError(DocuParseError):
    """The request never got an answer: DNS, TCP, TLS or a timeout."""


class InvalidRequest(DocuParseError):
    """400 / 404 / 409 — the request itself was wrong."""


class AuthenticationError(DocuParseError):
    """401 — missing, malformed, revoked or expired key."""


class PermissionDenied(DocuParseError):
    """403 that is not a quota problem."""


class QuotaExceeded(PermissionDenied):
    """403 ``quota_exceeded`` — the monthly document allowance is used up."""


class UnsupportedFile(DocuParseError):
    """413 / 415 — too large, too many pages, or not a supported type."""


class ExtractionFailed(DocuParseError):
    """422 — the document could not be turned into structured data.

    Not retryable: the same bytes and the same prompt produce the same answer.
    """


class RateLimited(DocuParseError):
    """429. ``retry_after`` is the server's own advice, in seconds."""

    def __init__(
        self, message: str, *, retry_after: Optional[float] = None, **kwargs: Any
    ):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ProviderUnavailable(DocuParseError):
    """503 — no extraction provider is configured or reachable.

    The server returns this rather than inventing invoice data. Retry later;
    if it persists, the deployment is misconfigured.
    """


class ServerError(DocuParseError):
    """5xx other than 503."""


# HTTP status → class. Codes the server may add later still land somewhere
# sensible, which is why this maps status and not the code string.
_BY_STATUS = {
    400: InvalidRequest,
    401: AuthenticationError,
    403: PermissionDenied,
    404: InvalidRequest,
    409: InvalidRequest,
    413: UnsupportedFile,
    415: UnsupportedFile,
    422: ExtractionFailed,
    429: RateLimited,
    503: ProviderUnavailable,
}


def error_from_response(
    status_code: int,
    body: Optional[Dict[str, Any]],
    *,
    retry_after: Optional[float] = None,
) -> DocuParseError:
    """Build the right exception from a failed response body."""
    payload = body or {}
    error = payload.get("error") or {}
    code = error.get("code") or f"http_{status_code}"
    message = error.get("message") or f"The API returned HTTP {status_code}."
    request_id = payload.get("request_id")
    details = error.get("details")

    if code == "quota_exceeded":
        cls = QuotaExceeded
    else:
        cls = _BY_STATUS.get(
            status_code, ServerError if status_code >= 500 else DocuParseError
        )

    kwargs: Dict[str, Any] = {
        "code": code,
        "status_code": status_code,
        "request_id": request_id,
        "details": details,
    }
    if cls is RateLimited:
        return RateLimited(message, retry_after=retry_after, **kwargs)
    return cls(message, **kwargs)
