from __future__ import annotations

import datetime as dt

from sqlalchemy import ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import ID, Base, UTCDateTime, utcnow
from app.utils.ids import prefixed_id


class DocumentBatch(Base):
    """A set of documents uploaded together.

    Deliberately thin: the batch owns no processing of its own. Each file
    becomes an ordinary document and an ordinary job, so a batch is just a
    label that makes progress reportable as one number. That keeps the
    worker, the retry policy and the webhooks identical whether a document
    arrived alone or in a folder of two hundred.
    """

    __tablename__ = "document_batches"
    __table_args__ = (
        Index("ix_document_batches_org_created", "organization_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        ID, primary_key=True, default=lambda: prefixed_id("bat")
    )
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    #: Files that were accepted and queued.
    document_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    #: Files that were rejected on arrival — wrong type, too large, unreadable.
    rejected_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
