"""What a case needs, and whether it has arrived.

This table is the product's core question made concrete. "Which clients are
blocked?" is a GROUP BY over these rows.

The state machine is explicit and enforced in code rather than inferred from
model output (§29). A document is ``RECEIVED`` because a file arrived and was
stored, ``VALID`` because deterministic rules passed — never because a model
said so.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import ID, Base, UTCDateTime, utcnow
from app.utils.ids import prefixed_id


class DocumentType:
    """Types the classifier may propose and a requirement may ask for."""

    PURCHASE_INVOICE = "purchase_invoice"
    SALES_INVOICE = "sales_invoice"
    BANK_STATEMENT = "bank_statement"
    CREDIT_NOTE = "credit_note"
    DEBIT_NOTE = "debit_note"
    GSTR_2B = "gstr_2b"
    GST_CERTIFICATE = "gst_certificate"
    PAN = "pan"
    AADHAAR = "aadhaar"
    ITR = "itr"
    TDS_CERTIFICATE = "tds_certificate"
    OTHER = "other"

    ALL = frozenset(
        {
            PURCHASE_INVOICE,
            SALES_INVOICE,
            BANK_STATEMENT,
            CREDIT_NOTE,
            DEBIT_NOTE,
            GSTR_2B,
            GST_CERTIFICATE,
            PAN,
            AADHAAR,
            ITR,
            TDS_CERTIFICATE,
            OTHER,
        }
    )

    #: Documents the invoice pipeline can already read end to end.
    INVOICE_LIKE = frozenset({PURCHASE_INVOICE, SALES_INVOICE, CREDIT_NOTE, DEBIT_NOTE})

    LABELS = {
        PURCHASE_INVOICE: "Purchase invoices",
        SALES_INVOICE: "Sales invoices",
        BANK_STATEMENT: "Bank statement",
        CREDIT_NOTE: "Credit notes",
        DEBIT_NOTE: "Debit notes",
        GSTR_2B: "GSTR-2B",
        GST_CERTIFICATE: "GST certificate",
        PAN: "PAN",
        AADHAAR: "Aadhaar",
        ITR: "ITR",
        TDS_CERTIFICATE: "TDS certificate",
        OTHER: "Other",
    }

    @classmethod
    def label(cls, document_type: str) -> str:
        return cls.LABELS.get(document_type, document_type.replace("_", " ").title())


class RequirementStatus:
    MISSING = "missing"
    REQUESTED = "requested"
    RECEIVED = "received"
    PROCESSING = "processing"
    VALID = "valid"
    INVALID = "invalid"
    NEEDS_REVIEW = "needs_review"
    WAIVED = "waived"

    ALL = frozenset(
        {MISSING, REQUESTED, RECEIVED, PROCESSING, VALID, INVALID, NEEDS_REVIEW, WAIVED}
    )

    #: Nothing further is owed by the client.
    SETTLED = frozenset({VALID, WAIVED})
    #: The firm is waiting on the client.
    OUTSTANDING = frozenset({MISSING, REQUESTED, INVALID})
    #: The firm is waiting on itself.
    WITH_THE_FIRM = frozenset({RECEIVED, PROCESSING, NEEDS_REVIEW})


#: Which transitions are legal. An illegal one is a bug, and raising beats
#: silently writing a state nobody can explain later.
_ALLOWED: dict[str, frozenset[str]] = {
    RequirementStatus.MISSING: frozenset(
        {RequirementStatus.REQUESTED, RequirementStatus.RECEIVED, RequirementStatus.WAIVED}
    ),
    RequirementStatus.REQUESTED: frozenset(
        {
            RequirementStatus.RECEIVED,
            RequirementStatus.MISSING,
            RequirementStatus.WAIVED,
            # Re-sending a reminder leaves it requested.
            RequirementStatus.REQUESTED,
        }
    ),
    RequirementStatus.RECEIVED: frozenset(
        {RequirementStatus.PROCESSING, RequirementStatus.NEEDS_REVIEW, RequirementStatus.INVALID}
    ),
    RequirementStatus.PROCESSING: frozenset(
        {
            RequirementStatus.VALID,
            RequirementStatus.INVALID,
            RequirementStatus.NEEDS_REVIEW,
        }
    ),
    RequirementStatus.NEEDS_REVIEW: frozenset(
        {RequirementStatus.VALID, RequirementStatus.INVALID, RequirementStatus.WAIVED}
    ),
    # A rejected document can be asked for again.
    RequirementStatus.INVALID: frozenset(
        {RequirementStatus.REQUESTED, RequirementStatus.RECEIVED, RequirementStatus.WAIVED}
    ),
    # A human can reopen a settled requirement; the agent cannot.
    RequirementStatus.VALID: frozenset({RequirementStatus.NEEDS_REVIEW}),
    RequirementStatus.WAIVED: frozenset({RequirementStatus.MISSING}),
}


class IllegalTransition(ValueError):
    """Raised when code tries to move a requirement somewhere it cannot go."""


def assert_transition(current: str, target: str) -> None:
    if target not in _ALLOWED.get(current, frozenset()):
        raise IllegalTransition(
            f"A requirement cannot go from {current!r} to {target!r}."
        )


def can_transition(current: str, target: str) -> bool:
    return target in _ALLOWED.get(current, frozenset())


class DocumentRequirement(Base):
    """One document a case is waiting for."""

    __tablename__ = "document_requirements"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "document_type", name="uq_document_requirements_case_id_document_type"
        ),
        Index("ix_document_requirements_org_status", "organization_id", "status"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: prefixed_id("req"))
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[str] = mapped_column(
        ID, ForeignKey("compliance_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )

    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    #: A requirement can be optional — present so the firm sees it, but not
    #: blocking. Only required ones make a case BLOCKED.
    required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=RequirementStatus.MISSING,
        server_default=text("'missing'"),
    )

    deadline: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    #: The document that satisfied this, once one has.
    received_document_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    #: Why it is invalid, or what the reviewer needs to look at. Plain text,
    #: written for the CA rather than for a log.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    requested_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    received_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    settled_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    @property
    def label(self) -> str:
        return DocumentType.label(self.document_type)

    @property
    def is_outstanding(self) -> bool:
        """True when the firm is still waiting on the client for this."""
        return self.required and self.status in RequirementStatus.OUTSTANDING

    def move_to(self, target: str, *, reason: str | None = None) -> None:
        """Transition, or raise. The only way this column should change."""
        assert_transition(self.status, target)
        self.status = target
        if reason is not None:
            self.reason = reason
        now = utcnow()
        if target == RequirementStatus.REQUESTED:
            self.requested_at = self.requested_at or now
        elif target == RequirementStatus.RECEIVED:
            self.received_at = now
        elif target in RequirementStatus.SETTLED:
            self.settled_at = now


#: What each kind of case asks for by default. Editable per case afterwards —
#: this is a starting point a CA recognises, not a rule they cannot change.
DEFAULT_REQUIREMENTS: dict[str, tuple[tuple[str, bool], ...]] = {
    "gst": (
        (DocumentType.SALES_INVOICE, True),
        (DocumentType.PURCHASE_INVOICE, True),
        (DocumentType.BANK_STATEMENT, True),
        (DocumentType.GSTR_2B, True),
        (DocumentType.CREDIT_NOTE, False),
    ),
    "itr": (
        (DocumentType.BANK_STATEMENT, True),
        (DocumentType.TDS_CERTIFICATE, True),
        (DocumentType.PAN, True),
    ),
    "tds": (
        (DocumentType.TDS_CERTIFICATE, True),
        (DocumentType.BANK_STATEMENT, True),
    ),
    "bookkeeping": (
        (DocumentType.BANK_STATEMENT, True),
        (DocumentType.PURCHASE_INVOICE, True),
        (DocumentType.SALES_INVOICE, True),
    ),
    "custom": (),
}
