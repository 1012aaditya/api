from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, String, false, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import ID, Base, UTCDateTime, utcnow
from app.utils.ids import usage_event_id


class UsageEvent(Base):
    """One billable/observable unit of work (§21, §34).

    Written for every authenticated API request, successful or not, so that
    quota, latency and estimated provider cost can be reported without
    re-reading document rows.
    """

    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_events_org_created", "organization_id", "created_at"),
        Index("ix_usage_events_org_billable", "organization_id", "billable", "created_at"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=usage_event_id)
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    api_key_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )
    document_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    endpoint: Mapped[str] = mapped_column(String(120), nullable=False)
    event_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="api_request", server_default=text("'api_request'")
    )
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    success: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # Whether this event consumes monthly document quota. A request rejected
    # before any provider work (bad file, rate limited) is not billable.
    billable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    pages: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)

    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
