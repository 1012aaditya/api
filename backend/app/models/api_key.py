from __future__ import annotations

import datetime as dt

from sqlalchemy import ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import ID, Base, UTCDateTime, utcnow
from app.utils.ids import api_key_id


class APIKey(Base):
    """An issued API credential.

    Only ``key_hash`` is stored. The plaintext key exists once, in the
    creation response, and is not recoverable afterwards (§17).
    """

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=api_key_id)
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    name: Mapped[str] = mapped_column(
        String(120), nullable=False, default="Default key", server_default=text("'Default key'")
    )
    environment: Mapped[str] = mapped_column(
        String(10), nullable=False, default="live", server_default=text("'live'")
    )

    # SHA-256 of the full plaintext key. Lookup is a single indexed equality
    # test; the key's own entropy (32 random bytes) is what makes the digest
    # safe without a slow KDF.
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # Display-only, so the dashboard can show "dp_live_a1b2…9f3c".
    prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    last_four: Mapped[str] = mapped_column(String(4), nullable=False)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Set when this key superseded another via rotation.
    rotated_from_id: Mapped[str | None] = mapped_column(ID, nullable=True)

    def is_usable(self, *, now: dt.datetime | None = None) -> bool:
        now = now or utcnow()
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= now:
            return False
        return True
