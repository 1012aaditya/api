"""The CA firm's own customers, and the work owed to them.

The tenancy has two levels now, and keeping them straight is the whole game:

    Organization  — the CA firm. The tenant. Who logs in.
      └── Client  — the firm's customer. Never logs in.

Every query in this file's repositories is scoped by ``organization_id``. A
client belongs to exactly one firm, and nothing in the agent layer can reach
across that line, because the organization is taken from the authenticated
session and is never an argument a model can supply.
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

from app.db.base import ID, Base, JSONType, UTCDateTime, utcnow
from app.utils.ids import prefixed_id


class ClientStatus:
    ACTIVE = "active"
    INACTIVE = "inactive"
    ON_HOLD = "on_hold"

    ALL = frozenset({ACTIVE, INACTIVE, ON_HOLD})


class Channel:
    WHATSAPP = "whatsapp"
    VOICE = "voice"
    EMAIL = "email"
    MANUAL = "manual"

    ALL = frozenset({WHATSAPP, VOICE, EMAIL, MANUAL})


class Language:
    EN = "en"
    HI = "hi"
    HINGLISH = "hinglish"

    ALL = frozenset({EN, HI, HINGLISH})


class ContactState:
    """Where the firm stands with this client on the current ask (§29)."""

    NOT_CONTACTED = "not_contacted"
    CONTACTED = "contacted"
    RESPONDED = "responded"
    COMMITTED = "committed"
    FOLLOW_UP_REQUIRED = "follow_up_required"
    ESCALATED = "escalated"
    OPTED_OUT = "opted_out"

    ALL = frozenset(
        {
            NOT_CONTACTED,
            CONTACTED,
            RESPONDED,
            COMMITTED,
            FOLLOW_UP_REQUIRED,
            ESCALATED,
            OPTED_OUT,
        }
    )


class Client(Base):
    """One customer of the CA firm."""

    __tablename__ = "clients"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "client_code", name="uq_clients_organization_id_client_code"
        ),
        Index("ix_clients_org_status", "organization_id", "status"),
        Index("ix_clients_org_gstin", "organization_id", "gstin"),
        # Inbound WhatsApp arrives as a phone number and nothing else, so
        # resolving number → client has to be an indexed lookup.
        Index("ix_clients_org_whatsapp", "organization_id", "whatsapp_phone"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: prefixed_id("cli"))
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    business_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    client_code: Mapped[str | None] = mapped_column(String(60), nullable=True)

    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    whatsapp_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    pan: Mapped[str | None] = mapped_column(String(10), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ClientStatus.ACTIVE, server_default=text("'active'")
    )

    # --- communication preferences (§37) ---------------------------------
    preferred_channel: Mapped[str] = mapped_column(
        String(20), nullable=False, default=Channel.WHATSAPP, server_default=text("'whatsapp'")
    )
    preferred_language: Mapped[str] = mapped_column(
        String(20), nullable=False, default=Language.EN, server_default=text("'en'")
    )
    #: False stops every automated message and call. Checked before each send,
    #: not only when the schedule is built — a client can opt out mid-sequence.
    allow_automated_contact: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    #: Set by a human, or by the agent hearing "do not contact me".
    automation_paused_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    contact_state: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=ContactState.NOT_CONTACTED,
        server_default=text("'not_contacted'"),
    )
    last_contacted_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_response_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    @property
    def display_name(self) -> str:
        return self.business_name or self.name

    def contactable_on(self, channel: str) -> bool:
        """Whether an *automated* message may go out on this channel.

        Deliberately strict: an opted-out or paused client is unreachable by
        the agent on every channel, and a channel with no address is not a
        channel. A human can still contact them by hand.
        """
        if not self.allow_automated_contact:
            return False
        if self.status != ClientStatus.ACTIVE:
            return False
        if channel == Channel.WHATSAPP:
            return bool(self.whatsapp_phone)
        if channel == Channel.VOICE:
            return bool(self.phone or self.whatsapp_phone)
        if channel == Channel.EMAIL:
            return bool(self.email)
        return False


class ClientFact(Base):
    """Structured agent memory (§10).

    Not a free-text scratchpad. Each fact is a named key with a source, a
    timestamp and a confidence, so "the client said they would send it
    tomorrow" is a row that can be shown, checked and expired — rather than a
    sentence buried in a transcript that the next prompt may or may not carry.

    A fact recorded by the agent never silently replaces one recorded by a
    human: ``source`` is part of the record and the UI shows it.
    """

    __tablename__ = "client_facts"
    __table_args__ = (
        UniqueConstraint("client_id", "key", name="uq_client_facts_client_id_key"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: prefixed_id("fact"))
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )

    key: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)

    #: "agent" | "user" | "system". A human's answer outranks a model's guess.
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="agent")
    confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: Some facts go stale — a commitment date is worthless after the date.
    expires_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class CaseType:
    GST = "gst"
    ITR = "itr"
    TDS = "tds"
    BOOKKEEPING = "bookkeeping"
    CUSTOM = "custom"

    ALL = frozenset({GST, ITR, TDS, BOOKKEEPING, CUSTOM})


class CaseStatus:
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    READY = "ready"
    COMPLETED = "completed"
    ESCALATED = "escalated"

    ALL = frozenset({NOT_STARTED, IN_PROGRESS, BLOCKED, READY, COMPLETED, ESCALATED})

    #: A case nobody should still be chasing documents for.
    TERMINAL = frozenset({COMPLETED})


class ComplianceCase(Base):
    """One period of work for one client: "GST, September 2026".

    Deliberately generic. GST monthly, ITR annual and TDS quarterly differ only
    in their ``type``, their ``period`` label and which documents they require
    — so one table carries all of them rather than three near-identical ones.
    """

    __tablename__ = "compliance_cases"
    __table_args__ = (
        UniqueConstraint(
            "client_id", "type", "period", name="uq_compliance_cases_client_id_type"
        ),
        Index("ix_compliance_cases_org_status", "organization_id", "status"),
        Index("ix_compliance_cases_org_deadline", "organization_id", "deadline"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: prefixed_id("case"))
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )

    type: Mapped[str] = mapped_column(String(30), nullable=False, default=CaseType.GST)
    #: A human-readable label the firm recognises: "2026-09", "FY 2025-26",
    #: "Q2 FY 2026-27". Not parsed — different case types count time
    #: differently and inventing a universal period type would be a fiction.
    period: Mapped[str] = mapped_column(String(40), nullable=False)
    deadline: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=CaseStatus.NOT_STARTED,
        server_default=text("'not_started'"),
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    completed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    @property
    def label(self) -> str:
        return f"{self.type.upper()} — {self.period}"
