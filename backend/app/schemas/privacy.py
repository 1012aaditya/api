"""Shapes for the privacy surface."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class PrivacyFootprint(BaseModel):
    """What this deployment holds, read from the deployment itself."""

    retention_days: int
    #: When the oldest unpurged document's bytes are due to be deleted.
    next_purge_at: dt.datetime | None
    generated_at: dt.datetime

    storage_backend: str
    #: "no_model" | "stays_here" | "cannot_be_proven". Deliberately three
    #: states: a hostname that cannot be shown to be private is not the same
    #: claim as a public API, and neither is the same as no model at all.
    #: Collapsing them into a bool would make one of the three a lie.
    document_locality: str
    #: What that means, and what to change if it is not what was wanted.
    locality_note: str
    model_endpoint: str | None
    extraction_configured: bool
    whatsapp_provider: str
    voice_provider: str

    counts: dict[str, int]
    #: Every third party this configuration sends client data to, and what
    #: each one receives. Empty means nothing leaves.
    processors: list[dict[str, str]] = Field(default_factory=list)


class ErasureReceiptOut(BaseModel):
    client_id: str
    client_name: str
    rows_deleted: int
    by_table: dict[str, int]
    #: Kept, with the link to a person broken. Billing history.
    unlinked: dict[str, int]
    objects_deleted: int
    objects_failed: int
    #: False when a stored file survived, so nobody believes the job is done.
    complete: bool
