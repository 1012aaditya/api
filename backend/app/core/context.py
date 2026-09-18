"""Per-request context carried out-of-band.

Holds the request id and the authenticated organization so that log records
and persisted rows can be correlated without threading the values through
every function signature.
"""

from __future__ import annotations

from contextvars import ContextVar

_request_id: ContextVar[str | None] = ContextVar("docuparse_request_id", default=None)
_organization_id: ContextVar[str | None] = ContextVar(
    "docuparse_organization_id", default=None
)


def set_request_id(value: str) -> None:
    _request_id.set(value)


def get_request_id() -> str | None:
    return _request_id.get()


def set_organization_id(value: str | None) -> None:
    _organization_id.set(value)


def get_organization_id() -> str | None:
    return _organization_id.get()
