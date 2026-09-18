"""The public error contract (§19).

Every failure a client can observe is one of these. Each carries a stable
machine-readable ``code``, an HTTP status, and a message written for the
developer integrating against us. Provider errors, driver exceptions and
stack traces are translated here and never leak outward.
"""

from __future__ import annotations

from typing import Any


class DocuParseError(Exception):
    """Base class for every error with a defined public representation."""

    code: str = "internal_error"
    status_code: int = 500
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.details = details or {}
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details
        return error


# --- 400 ---------------------------------------------------------------


class InvalidRequestError(DocuParseError):
    code = "invalid_request"
    status_code = 400
    message = "The request was malformed."


class InvalidFileError(DocuParseError):
    code = "invalid_file"
    status_code = 400
    message = "The uploaded file could not be read as a valid document."


# --- 401 / 403 ---------------------------------------------------------


class AuthenticationRequiredError(DocuParseError):
    code = "authentication_required"
    status_code = 401
    message = "Provide an API key as 'Authorization: Bearer dp_live_...'."


class InvalidAPIKeyError(DocuParseError):
    code = "invalid_api_key"
    status_code = 401
    message = "The API key is invalid, revoked, or expired."


class ForbiddenError(DocuParseError):
    code = "forbidden"
    status_code = 403
    message = "This key is not permitted to perform that action."


class QuotaExceededError(DocuParseError):
    code = "quota_exceeded"
    status_code = 403
    message = "The monthly document quota for this organization has been exhausted."


# --- 404 / 409 ---------------------------------------------------------


class NotFoundError(DocuParseError):
    code = "not_found"
    status_code = 404
    message = "The requested resource does not exist."


class ConflictError(DocuParseError):
    code = "conflict"
    status_code = 409
    message = "The resource already exists."


# --- 413 / 415 ---------------------------------------------------------


class FileTooLargeError(DocuParseError):
    code = "file_too_large"
    status_code = 413
    message = "The uploaded file exceeds the maximum allowed size."


class UnsupportedFileTypeError(DocuParseError):
    code = "unsupported_file_type"
    status_code = 415
    message = "Only PDF, PNG, JPG and JPEG files are supported."


class TooManyPagesError(DocuParseError):
    code = "too_many_pages"
    status_code = 413
    message = "The document has more pages than this endpoint accepts."


# --- 422 ---------------------------------------------------------------


class ExtractionFailedError(DocuParseError):
    code = "extraction_failed"
    status_code = 422
    message = "The document could not be parsed into structured invoice data."


# --- 429 ---------------------------------------------------------------


class RateLimitExceededError(DocuParseError):
    code = "rate_limit_exceeded"
    status_code = 429
    message = "Rate limit exceeded."

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_seconds: int = 60,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.retry_after_seconds = retry_after_seconds


# --- 503 ---------------------------------------------------------------


class ProviderUnavailableError(DocuParseError):
    """No AI provider is configured, or the configured one cannot be reached.

    This exists so that a missing integration surfaces as an explicit,
    debuggable failure. Returning invented invoice data in this situation
    would be worse than returning nothing.
    """

    code = "extraction_provider_unavailable"
    status_code = 503
    message = (
        "The document extraction provider is not configured or is currently unreachable."
    )
