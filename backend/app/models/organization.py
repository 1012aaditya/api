from __future__ import annotations

import datetime as dt

from sqlalchemy import Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import ID, Base, UTCDateTime, utcnow
from app.utils.ids import organization_id


class Organization(Base):
    """The tenant boundary. Every customer-owned row hangs off one of these."""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=organization_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)

    # Billing-ready, but nothing charges against it yet (§36).
    plan: Mapped[str] = mapped_column(
        String(40), nullable=False, default="free", server_default=text("'free'")
    )

    # Per-organization overrides. NULL means "use the deployment default".
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_document_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
