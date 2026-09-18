from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import ID, Base, JSONType, UTCDateTime, utcnow
from app.utils.ids import extraction_id


class ExtractionStatus:
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Extraction(Base):
    """One attempt at turning a document into structured data.

    ``data`` holds the normalized, schema-valid payload. ``field_confidence``
    holds the per-field scores behind it (§8), kept alongside rather than
    inside so the public payload stays easy to consume.
    """

    __tablename__ = "extractions"
    __table_args__ = (Index("ix_extractions_org_created", "organization_id", "created_at"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=extraction_id)
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(
        ID, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    document_type: Mapped[str] = mapped_column(
        String(60), nullable=False, default="gst_invoice", server_default=text("'gst_invoice'")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    data: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    field_confidence: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    overall_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)

    provider_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
