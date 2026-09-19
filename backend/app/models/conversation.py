"""WhatsApp and voice threads, and the agent's own scheduled work.

Messages are stored because the CA needs to see what was said in their name.
The agent's claim that it "asked for the bank statement" is worth nothing
without the message that proves it.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import ID, Base, JSONType, UTCDateTime, utcnow
from app.utils.ids import prefixed_id


class Direction:
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageType:
    TEXT = "text"
    DOCUMENT = "document"
    IMAGE = "image"
    TEMPLATE = "template"
    SYSTEM = "system"


class MessageStatus:
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    RECEIVED = "received"


class ConversationStatus:
    OPEN = "open"
    AWAITING_CLIENT = "awaiting_client"
    AWAITING_FIRM = "awaiting_firm"
    CLOSED = "closed"


class Conversation(Base):
    """One ongoing thread with one client on one channel."""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "client_id", "channel", name="uq_conversations_client_channel"
        ),
        Index("ix_conversations_org_last_message", "organization_id", "last_message_at"),
        # Inbound webhooks arrive with a phone number and nothing else.
        Index("ix_conversations_org_phone", "organization_id", "phone"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: prefixed_id("conv"))
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )

    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="whatsapp")
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=ConversationStatus.OPEN,
        server_default=text("'open'"),
    )

    last_message_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_inbound_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class Message(Base):
    """One message, in either direction."""

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        # The provider's own id is how a redelivered webhook is recognised as
        # the same message rather than processed twice (§18, §23).
        UniqueConstraint(
            "organization_id", "provider_message_id", name="uq_messages_provider_message_id"
        ),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: prefixed_id("msg"))
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        ID, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("compliance_cases.id", ondelete="SET NULL"), nullable=True
    )

    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False, default=MessageType.TEXT)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: An inbound document becomes a Document row; this links the two.
    document_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    media_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)

    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MessageStatus.QUEUED, server_default=text("'queued'")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: What the agent understood, when it was inbound. Never the source of
    #: truth for state — a recorded reading, kept so a human can check it.
    detected_intent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    intent_details: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)

    #: False when a human wrote it in the dashboard.
    sent_by_agent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class CallStatus:
    SCHEDULED = "scheduled"
    DIALING = "dialing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    NO_ANSWER = "no_answer"
    FAILED = "failed"
    CANCELLED = "cancelled"

    TERMINAL = frozenset({COMPLETED, NO_ANSWER, FAILED, CANCELLED})


class CallIntent:
    """What the client said, as understood from the call (§9)."""

    DOCUMENT_SENT = "document_sent"
    DOCUMENT_COMMITMENT = "document_commitment"
    DOCUMENT_NOT_AVAILABLE = "document_not_available"
    CLIENT_CONFUSED = "client_confused"
    CLIENT_WANTS_HUMAN = "client_wants_human"
    WRONG_NUMBER = "wrong_number"
    DO_NOT_CONTACT = "do_not_contact"
    OTHER = "other"

    ALL = frozenset(
        {
            DOCUMENT_SENT,
            DOCUMENT_COMMITMENT,
            DOCUMENT_NOT_AVAILABLE,
            CLIENT_CONFUSED,
            CLIENT_WANTS_HUMAN,
            WRONG_NUMBER,
            DO_NOT_CONTACT,
            OTHER,
        }
    )
    #: Intents that must reach a person immediately.
    ESCALATING = frozenset({CLIENT_WANTS_HUMAN, WRONG_NUMBER, DO_NOT_CONTACT})


class Call(Base):
    """One voice call attempt.

    A call is recorded whether or not it connected. "We tried three times and
    nobody answered" is information the CA needs, and it is the only honest
    basis for escalating to a human.
    """

    __tablename__ = "calls"
    __table_args__ = (
        Index("ix_calls_org_created", "organization_id", "created_at"),
        UniqueConstraint("organization_id", "provider_call_id", name="uq_calls_provider_call_id"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: prefixed_id("call"))
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("compliance_cases.id", ondelete="SET NULL"), nullable=True
    )

    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CallStatus.SCHEDULED,
        server_default=text("'scheduled'"),
    )
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    provider_call_id: Mapped[str | None] = mapped_column(String(200), nullable=True)

    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_intent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    intent_details: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)

    started_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ended_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class AgentJobStatus:
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED})


class AgentJobType:
    SEND_FOLLOWUP = "send_followup"
    PLACE_CALL = "place_call"
    CHECK_CASE = "check_case"
    PROCESS_INBOUND = "process_inbound"


class AgentJob(Base):
    """Time-based work for the agent: follow-ups, calls, case re-checks.

    A separate table from ``extraction_jobs`` on purpose. That one requires a
    ``document_id`` — correct for extraction, meaningless for "remind this
    client on Thursday" — and widening it would loosen a well-constrained
    table that the existing suite depends on. The *mechanics* are shared:
    ``FOR UPDATE SKIP LOCKED``, attempts, backoff, ``available_at``, and the
    same worker process drains both queues.
    """

    __tablename__ = "agent_jobs"
    __table_args__ = (
        Index("ix_agent_jobs_claim", "status", "available_at"),
        Index("ix_agent_jobs_org_created", "organization_id", "created_at"),
        # One pending job of a kind per case: re-running the scheduler must
        # not stack five reminders for the same missing document.
        UniqueConstraint("dedupe_key", name="uq_agent_jobs_dedupe_key"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: prefixed_id("ajob"))
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True
    )
    case_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("compliance_cases.id", ondelete="CASCADE"), nullable=True, index=True
    )

    type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=AgentJobStatus.QUEUED,
        server_default=text("'queued'"),
    )
    #: When this becomes runnable. The whole follow-up schedule is expressed
    #: as rows with future timestamps rather than a sleeping process.
    available_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, index=True
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )
    #: Which follow-up in the sequence this is, for the per-case cap.
    attempt_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
