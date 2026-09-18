from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import ID, Base, JSONType, UTCDateTime, utcnow
from app.utils.ids import prefixed_id


class ValidationResult(Base):
    """The outcome of the business-validation pass over one extraction."""

    __tablename__ = "validation_results"

    id: Mapped[str] = mapped_column(
        ID, primary_key=True, default=lambda: prefixed_id("val")
    )
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    extraction_id: Mapped[str] = mapped_column(
        ID, ForeignKey("extractions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    overall: Mapped[str] = mapped_column(String(20), nullable=False)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, nullable=False, default=list)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
