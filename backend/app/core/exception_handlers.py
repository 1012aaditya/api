"""Translate every exception into the public error contract (§19).

Nothing below returns a stack trace, a driver message, or a provider URL to
the client. Unexpected exceptions are logged in full server-side and reduced
to a generic ``internal_error`` on the wire.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import get_request_id
from app.core.errors import DocuParseError, RateLimitExceededError
from app.core.logging import get_logger

logger = get_logger("docuparse.errors")

# Starlette raises bare HTTPExceptions for routing-level failures. Map the
# ones a client can actually trigger onto our own codes.
_STATUS_TO_CODE = {
    400: "invalid_request",
    401: "authentication_required",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "file_too_large",
    415: "unsupported_file_type",
    422: "invalid_request",
    429: "rate_limit_exceeded",
}


def _render(
    status_code: int,
    code: str,
    message: str,
    *,
    details: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, object] = {"code": code, "message": message}
    if details:
        error["details"] = details
    response_headers = {"x-docuparse-error-code": code}
    if headers:
        response_headers |= headers
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "request_id": get_request_id(), "error": error},
        headers=response_headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DocuParseError)
    async def _docuparse_error(_request: Request, exc: DocuParseError) -> JSONResponse:
        headers = None
        if isinstance(exc, RateLimitExceededError):
            headers = {"Retry-After": str(exc.retry_after_seconds)}
        if exc.status_code >= 500:
            logger.error("error.server", error_code=exc.code, message=exc.message)
        else:
            logger.info("error.client", error_code=exc.code)
        return _render(
            exc.status_code, exc.code, exc.message, details=exc.details, headers=headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Pydantic's error list is useful to a developer and contains no
        # secrets, but it can contain submitted values — keep locations only.
        fields = [
            {
                "location": ".".join(str(part) for part in err.get("loc", ())),
                "message": err.get("msg", "invalid value"),
            }
            for err in exc.errors()[:10]
        ]
        return _render(
            400,
            "invalid_request",
            "The request could not be validated.",
            details={"fields": fields},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, "internal_error")
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _render(exc.status_code, code, detail)

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("error.unhandled", error=type(exc).__name__)
        return _render(
            500,
            "internal_error",
            "An unexpected error occurred. Quote the request_id when contacting support.",
        )
