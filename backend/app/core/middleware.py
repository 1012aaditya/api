"""Request-scoped middleware: identity, timing, and access logging."""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import get_organization_id, set_organization_id, set_request_id
from app.core.logging import get_logger
from app.utils.ids import request_id as new_request_id

logger = get_logger("docuparse.access")

# Header a client may send to supply its own correlation id. We only honour
# it when it looks like an id, so a client cannot inject newlines into logs.
_INBOUND_HEADER = "x-request-id"
_MAX_INBOUND_LENGTH = 64


def _sanitize(candidate: str | None) -> str | None:
    if not candidate:
        return None
    candidate = candidate.strip()
    if not candidate or len(candidate) > _MAX_INBOUND_LENGTH:
        return None
    if not all(c.isalnum() or c in "-_" for c in candidate):
        return None
    return candidate


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, logs one structured line per request (§20, §31)."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = _sanitize(request.headers.get(_INBOUND_HEADER)) or new_request_id()
        set_request_id(request_id)
        set_organization_id(None)
        request.state.request_id = request_id

        started = time.perf_counter()
        status_code = 500
        error_code: str | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            error_code = response.headers.get("x-docuparse-error-code")
            response.headers["X-Request-Id"] = request_id
            return response
        finally:
            logger.info(
                "request.completed",
                endpoint=request.url.path,
                method=request.method,
                status=status_code,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                organization_id=get_organization_id(),
                error_code=error_code,
            )
