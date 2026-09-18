"""Structured JSON logging (§31).

Log records carry request_id, organization_id, endpoint, status, latency,
provider, model and error_code. They deliberately do NOT carry API keys,
document bytes, extracted invoice values, or provider credentials (§23).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.core.context import get_organization_id, get_request_id

# Never let these reach a log sink, whatever a call site passes.
_REDACTED_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "password",
        "secret",
        "token",
        "key_hash",
        "ai_api_key",
        "s3_secret_access_key",
        "jwt_secret",
        "webhook_secret",
        "raw_response",
        "document_bytes",
        "invoice_data",
    }
)


def _bind_context(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    request_id = get_request_id()
    if request_id and "request_id" not in event_dict:
        event_dict["request_id"] = request_id
    organization_id = get_organization_id()
    if organization_id and "organization_id" not in event_dict:
        event_dict["organization_id"] = organization_id
    return event_dict


def _redact(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict):
        if key.lower() in _REDACTED_KEYS:
            event_dict[key] = "[redacted]"
    return event_dict


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _bind_context,
            _redact,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level.upper())
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "docuparse") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


# structlog's bound logger takes the log message as a positional parameter
# named ``event``, so a context key called ``event`` collides with it and
# raises at the call site. Anything domain-specific gets a qualified name
# (``webhook_event``, ``event_type``) instead.
RESERVED_LOG_KEYS = frozenset({"event"})
