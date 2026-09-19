"""Exceptions, tasks, the agent's audit trail, and the agent's leash.

Four tables that together make the agent trustworthy rather than merely
capable:

* ``exceptions`` — everything the system could not resolve on its own, queued
  for a human. This is the product's honesty valve: when a rule cannot decide,
  it does not guess, it files one of these.
* ``tasks`` — work assigned to a person.
* ``agent_events`` — an append-only record of what the agent did and why.
* ``agent_policies`` — what the agent is allowed to do, and how often.
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
    false,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import ID, Base, JSONType, UTCDateTime, utcnow
from app.utils.ids import prefixed_id


class Severity:
    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"

    ALL = frozenset({INFO, WARNING, HIGH, CRITICAL})
    #: Severities that should stop a case being marked ready.
    BLOCKING = frozenset({HIGH, CRITICAL})


class ExceptionStatus:
    OPEN = "open"
    IN_REVIEW = "in_review"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"

    ALL = frozenset({OPEN, IN_REVIEW, RESOLVED, DISMISSED})
    CLOSED = frozenset({RESOLVED, DISMISSED})


class ExceptionType:
    """Why a human is being asked to look."""

    GSTIN_MISMATCH = "gstin_mismatch"
    CLASSIFICATION_UNCERTAIN = "classification_uncertain"
    VALIDATION_FAILED = "validation_failed"
    ARITHMETIC_MISMATCH = "arithmetic_mismatch"
    DUPLICATE_DOCUMENT = "duplicate_document"
    PERIOD_MISMATCH = "period_mismatch"
    UNREADABLE_DOCUMENT = "unreadable_document"
    WRONG_DOCUMENT_TYPE = "wrong_document_type"
    CLIENT_REQUESTED_HUMAN = "client_requested_human"
    CLIENT_OPTED_OUT = "client_opted_out"
    WRONG_NUMBER = "wrong_number"
    MAX_FOLLOWUPS_REACHED = "max_followups_reached"
    OTHER = "other"


class ReviewException(Base):
    """Something the system would not decide by itself.

    Named ``ReviewException`` in Python so it cannot be confused with a raised
    exception; the table and the product call it an exception.
    """

    __tablename__ = "exceptions"
    __table_args__ = (
        Index("ix_exceptions_org_status", "organization_id", "status"),
        Index("ix_exceptions_org_created", "organization_id", "created_at"),
        # An agent retrying the same finding must not pile up duplicates.
        UniqueConstraint("organization_id", "dedupe_key", name="uq_exceptions_dedupe_key"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: prefixed_id("exc"))
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True
    )
    case_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("compliance_cases.id", ondelete="CASCADE"), nullable=True, index=True
    )
    document_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    requirement_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("document_requirements.id", ondelete="SET NULL"), nullable=True
    )

    type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(
        String(20), nullable=False, default=Severity.WARNING, server_default=text("'warning'")
    )
    #: Written for the CA, in their words, not a stack trace.
    message: Mapped[str] = mapped_column(Text, nullable=False)
    #: The evidence. Whatever a reviewer needs to decide without opening the
    #: database: the two GSTINs that differ, the totals that do not add up.
    details: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)

    #: Stable identity of the *finding*, so re-running the same check updates
    #: rather than duplicates. Usually type + document or requirement id.
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ExceptionStatus.OPEN, server_default=text("'open'")
    )
    assigned_to_user_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by_user_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    @property
    def is_open(self) -> bool:
        return self.status not in ExceptionStatus.CLOSED


class TaskStatus:
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"

    ALL = frozenset({OPEN, IN_PROGRESS, DONE, CANCELLED})
    CLOSED = frozenset({DONE, CANCELLED})


class Priority:
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"

    ALL = frozenset({LOW, NORMAL, HIGH, URGENT})


class Task(Base):
    """Work for a person, created by the agent or by a colleague."""

    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_org_status", "organization_id", "status"),
        Index("ix_tasks_org_due", "organization_id", "due_at"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: prefixed_id("task"))
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True
    )
    case_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("compliance_cases.id", ondelete="CASCADE"), nullable=True
    )
    exception_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("exceptions.id", ondelete="CASCADE"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, default=Priority.NORMAL, server_default=text("'normal'")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaskStatus.OPEN, server_default=text("'open'")
    )
    assigned_to_user_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(
        String(20), nullable=False, default="agent", server_default=text("'agent'")
    )

    due_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class ActorType:
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"
    CLIENT = "client"

    ALL = frozenset({USER, AGENT, SYSTEM, CLIENT})


class AgentEvent(Base):
    """Append-only record of what happened (§15).

    Written for the CA to read, not only for debugging. "Agent sent WhatsApp",
    "client uploaded bank_statement.pdf", "validation passed", "case moved to
    READY" — a timeline a person can follow without trusting anyone's summary
    of it.

    Nothing updates or deletes these rows. If the agent was wrong, that is a
    new event saying so, not an edit to the old one.
    """

    __tablename__ = "agent_events"
    __table_args__ = (
        Index("ix_agent_events_org_created", "organization_id", "created_at"),
        Index("ix_agent_events_client_created", "client_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: prefixed_id("evt"))
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True
    )
    case_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("compliance_cases.id", ondelete="CASCADE"), nullable=True, index=True
    )

    actor_type: Mapped[str] = mapped_column(String(20), nullable=False, default=ActorType.AGENT)
    actor_id: Mapped[str | None] = mapped_column(ID, nullable=True)

    #: A stable verb: "whatsapp.sent", "document.classified", "case.updated".
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    #: One line a human can read without expanding anything.
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    entity_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    #: Never the document itself, never a full transcript, never credentials.
    details: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)

    #: Groups every event from one agent run, so a timeline can be collapsed.
    run_id: Mapped[str | None] = mapped_column(ID, nullable=True, index=True)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class AgentPolicy(Base):
    """What the agent may do for one firm, and how much (§17).

    Every automated action checks this first. The defaults are deliberately
    conservative: a firm turns capabilities *on* after watching the agent
    work, rather than discovering what it did overnight.
    """

    __tablename__ = "agent_policies"

    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )

    #: The master switch. False means the agent observes and files exceptions
    #: but contacts nobody.
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    allow_whatsapp: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    #: Off by default. A phone call is the most intrusive thing this product
    #: does, and it should be a decision somebody made on purpose.
    allow_voice_calls: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    allow_document_processing: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    allow_auto_followup: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    allow_auto_escalation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    # --- rate caps: the actual leash ------------------------------------
    max_messages_per_day: Mapped[int] = mapped_column(
        Integer, nullable=False, default=200, server_default=text("200")
    )
    max_calls_per_day: Mapped[int] = mapped_column(
        Integer, nullable=False, default=20, server_default=text("20")
    )
    max_followups_per_case: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )

    # --- follow-up timing (§11), configurable, never hard-coded ---------
    first_reminder_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=24, server_default=text("24")
    )
    second_reminder_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=48, server_default=text("48")
    )
    voice_call_after_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=72, server_default=text("72")
    )

    #: Below this, a classification is never acted on automatically (§G).
    classification_threshold: Mapped[str] = mapped_column(
        String(10), nullable=False, default="0.80", server_default=text("'0.80'")
    )
    #: When true, no client data may leave the deployment (§19). A model
    #: reached over the public internet is refused rather than used, and the
    #: work waits for a person instead.
    local_ai_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    #: Whether a model may decide what to do next about a case, or only the
    #: ladder may. Off means the deterministic rules run, exactly as they did
    #: before there was a planner — which is also what happens when no model
    #: is configured at all.
    allow_ai_planning: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    #: Outside these hours the agent queues instead of sending. Nobody wants a
    #: compliance reminder at 2am.
    quiet_hours_start: Mapped[int] = mapped_column(
        Integer, nullable=False, default=21, server_default=text("21")
    )
    quiet_hours_end: Mapped[int] = mapped_column(
        Integer, nullable=False, default=9, server_default=text("9")
    )

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    def threshold(self) -> float:
        try:
            return float(self.classification_threshold)
        except (TypeError, ValueError):
            return 0.80
