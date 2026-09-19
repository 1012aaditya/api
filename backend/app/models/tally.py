"""Tally integration: the customer's chart of accounts, and how to post to it.

Extraction produces a supplier *name* printed on a page. Tally posts to a
*ledger* in the customer's own books. Nothing in the invoice tells you which
ledger that is — "ACME TRADERS PVT LTD" on the page might be "Acme Traders"
in their ledger master, or "Acme Traders - Pune", or a ledger they have not
created yet.

Bridging that gap is what these three tables are for, and it is the part that
gets better with use: every match a human confirms is remembered, so the same
supplier resolves itself next month.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint, text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import ID, Base, UTCDateTime, utcnow
from app.utils.ids import prefixed_id


class Ledger(Base):
    """One account in the customer's Tally ledger master.

    Imported from their books, never invented by us. We only ever *select*
    from this list — a supplier we cannot find here is reported as unmatched
    rather than posted to a ledger we made up.
    """

    __tablename__ = "ledgers"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_ledgers_organization_id_name"),
        Index("ix_ledgers_org_gstin", "organization_id", "gstin"),
        Index("ix_ledgers_org_normalized", "organization_id", "normalized_name"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: prefixed_id("led"))
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: Exactly as Tally holds it. This string is what goes into the voucher
    #: XML, so it must survive the round trip byte for byte.
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    #: Case-folded, punctuation- and suffix-stripped. Only for matching.
    normalized_name: Mapped[str] = mapped_column(String(300), nullable=False)

    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    #: Tally's group ("Sundry Creditors", "Duties & Taxes", …). Decides which
    #: ledgers are offerable as a supplier and which as a tax account.
    parent_group: Mapped[str | None] = mapped_column(String(200), nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class LedgerAlias(Base):
    """A confirmed mapping from something printed on an invoice to a ledger.

    This is the table that compounds. A fuzzy match is a guess and is never
    written here; only a human confirmation is. After a few months of use the
    customer's own corrections make this deployment better for *them* than any
    fresh install could be, which is the entire point.
    """

    __tablename__ = "ledger_aliases"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "key_type",
            "match_key",
            name="uq_ledger_aliases_organization_id_key_type",
        ),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: prefixed_id("lal"))
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ledger_id: Mapped[str] = mapped_column(
        ID, ForeignKey("ledgers.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: "gstin" or "name". A GSTIN alias is worth far more — it survives the
    #: supplier renaming themselves, and a name alias does not.
    key_type: Mapped[str] = mapped_column(String(20), nullable=False)
    match_key: Mapped[str] = mapped_column(String(300), nullable=False)

    #: Who confirmed it, for the audit trail. Never a machine.
    confirmed_by_user_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class TallySettings(Base):
    """Where a purchase voucher's non-supplier legs go, for one organization.

    Every field here is a ledger *name* rather than a foreign key, because
    Tally matches on the name at import time and the customer may create the
    ledger after configuring this. Names are validated against the imported
    master when a voucher is built, not when the setting is saved.
    """

    __tablename__ = "tally_settings"

    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )

    #: Tally refuses an import whose company name does not match the open
    #: company, so this has to be exact.
    company_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    voucher_type: Mapped[str] = mapped_column(
        String(100), nullable=False, default="Purchase", server_default=text("'Purchase'")
    )

    purchase_ledger: Mapped[str | None] = mapped_column(String(300), nullable=True)
    cgst_ledger: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sgst_ledger: Mapped[str | None] = mapped_column(String(300), nullable=True)
    igst_ledger: Mapped[str | None] = mapped_column(String(300), nullable=True)
    utgst_ledger: Mapped[str | None] = mapped_column(String(300), nullable=True)
    cess_ledger: Mapped[str | None] = mapped_column(String(300), nullable=True)
    round_off_ledger: Mapped[str | None] = mapped_column(String(300), nullable=True)
    other_charges_ledger: Mapped[str | None] = mapped_column(String(300), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    def is_configured(self) -> bool:
        """The minimum needed to post anything at all."""
        return bool(self.purchase_ledger)
