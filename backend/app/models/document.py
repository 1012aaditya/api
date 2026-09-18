from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import ID, Base, UTCDateTime, utcnow
from app.utils.ids import document_id


class DocumentStatus:
    RECEIVED = "received"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    PURGED = "purged"


class Document(Base):
    """An uploaded file.

    The row outlives the bytes: when retention expires, the object is deleted
    from storage and the row is marked ``purged`` so usage history and support
    lookups survive without keeping customer documents around (§24).
    """

    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_org_created", "organization_id", "created_at"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=document_id)
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    filename: Mapped[str] = mapped_column(String(400), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    storage_backend: Mapped[str] = mapped_column(
        String(20), nullable=False, default="local", server_default=text("'local'")
    )
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)

    document_type: Mapped[str] = mapped_column(
        String(60), nullable=False, default="gst_invoice", server_default=text("'gst_invoice'")
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DocumentStatus.RECEIVED,
        server_default=text("'received'"),
    )

    retention_expires_at: Mapped[dt.datetime | None] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    purged_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
