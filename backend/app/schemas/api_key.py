from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field


class CreateAPIKeyRequest(BaseModel):
    name: str = Field(default="Default key", max_length=120)
    environment: Literal["live", "test"] = "live"
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class APIKeySummary(BaseModel):
    """What a key looks like after creation. The secret is not recoverable."""

    id: str
    name: str
    environment: str
    masked_key: str
    created_at: dt.datetime
    last_used_at: dt.datetime | None
    revoked_at: dt.datetime | None
    expires_at: dt.datetime | None
    active: bool


class CreatedAPIKey(APIKeySummary):
    """Returned exactly once, at creation or rotation."""

    key: str = Field(
        ...,
        description=(
            "The full secret. Store it now — it is hashed on our side and "
            "cannot be shown again."
        ),
    )
