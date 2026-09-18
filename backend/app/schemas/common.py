"""Envelopes shared by every endpoint."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorBody(BaseModel):
    code: str = Field(..., examples=["rate_limit_exceeded"])
    message: str = Field(..., examples=["Rate limit exceeded."])
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    success: bool = False
    request_id: str | None = None
    error: ErrorBody


class SuccessResponse(BaseModel, Generic[T]):
    success: bool = True
    request_id: str
    data: T
