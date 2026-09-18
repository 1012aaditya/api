from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, ForeignKey, String, text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import ID, Base, UTCDateTime, utcnow
from app.utils.ids import user_id


class User(Base):
    """A dashboard login. API traffic authenticates with API keys, not users."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=user_id)
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str] = mapped_column(
        String(40), nullable=False, default="owner", server_default=text("'owner'")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    last_login_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
