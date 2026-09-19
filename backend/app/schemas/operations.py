"""Request and response shapes for the CA operations dashboard."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class ClientIn(BaseModel):
    name: str = Field(..., max_length=200)
    business_name: str | None = Field(default=None, max_length=200)
    client_code: str | None = Field(default=None, max_length=60)
    phone: str | None = Field(default=None, max_length=20)
    whatsapp_phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=320)
    gstin: str | None = Field(default=None, max_length=15)
    pan: str | None = Field(default=None, max_length=10)
    preferred_language: str = "en"
    preferred_channel: str = "whatsapp"
    allow_automated_contact: bool = True
    notes: str | None = None


class ClientPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    business_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=20)
    whatsapp_phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=320)
    gstin: str | None = Field(default=None, max_length=15)
    pan: str | None = Field(default=None, max_length=10)
    status: str | None = None
    preferred_language: str | None = None
    preferred_channel: str | None = None
    allow_automated_contact: bool | None = None
    notes: str | None = None


class ClientOut(BaseModel):
    id: str
    name: str
    business_name: str | None
    client_code: str | None
    phone: str | None
    whatsapp_phone: str | None
    email: str | None
    gstin: str | None
    pan: str | None
    status: str
    preferred_channel: str
    preferred_language: str
    allow_automated_contact: bool
    automation_paused_reason: str | None
    contact_state: str
    last_contacted_at: dt.datetime | None
    last_response_at: dt.datetime | None
    created_at: dt.datetime
    #: Filled on the list view so the dashboard can rank by urgency.
    open_cases: int = 0
    blocked_cases: int = 0


class RequirementOut(BaseModel):
    id: str
    document_type: str
    label: str
    required: bool
    status: str
    reason: str | None = None
    received_document_id: str | None = None
    requested_at: dt.datetime | None = None
    received_at: dt.datetime | None = None


class CaseIn(BaseModel):
    client_id: str
    type: str = "gst"
    period: str = Field(..., max_length=40)
    deadline: dt.date | None = None
    #: Override the defaults for this case. Omit to use them.
    document_types: list[str] | None = None


class CaseOut(BaseModel):
    id: str
    client_id: str
    client_name: str | None = None
    type: str
    period: str
    label: str
    deadline: dt.date | None
    status: str
    created_at: dt.datetime
    requirements: list[RequirementOut] = Field(default_factory=list)
    #: Labels, not slugs — this list is read by the firm, not by code.
    outstanding: list[str] = Field(default_factory=list)
    open_exceptions: int = 0


class ExceptionOut(BaseModel):
    id: str
    type: str
    severity: str
    message: str
    status: str
    client_id: str | None
    client_name: str | None = None
    case_id: str | None
    document_id: str | None
    details: dict = Field(default_factory=dict)
    created_at: dt.datetime
    resolved_at: dt.datetime | None = None


class ResolveExceptionIn(BaseModel):
    status: str = "resolved"
    note: str | None = None


class TaskOut(BaseModel):
    id: str
    title: str
    description: str | None
    priority: str
    status: str
    client_id: str | None
    client_name: str | None = None
    case_id: str | None
    due_at: dt.datetime | None
    created_by: str
    created_at: dt.datetime


class TaskIn(BaseModel):
    title: str = Field(..., max_length=300)
    description: str | None = None
    client_id: str | None = None
    case_id: str | None = None
    priority: str = "normal"
    due_at: dt.datetime | None = None


class AgentEventOut(BaseModel):
    id: str
    actor_type: str
    action: str
    summary: str
    client_id: str | None
    client_name: str | None = None
    case_id: str | None
    details: dict = Field(default_factory=dict)
    created_at: dt.datetime


class MessageOut(BaseModel):
    id: str
    direction: str
    type: str
    body: str | None
    status: str
    detected_intent: str | None
    sent_by_agent: bool
    created_at: dt.datetime


class ConversationOut(BaseModel):
    id: str
    client_id: str
    client_name: str | None = None
    channel: str
    status: str
    last_message_at: dt.datetime | None
    messages: list[MessageOut] = Field(default_factory=list)


class CommandCentre(BaseModel):
    """What needs attention today (§13).

    Counts of things a person can act on. No vanity metrics: there is no
    "documents processed all time" here, because nobody does anything
    differently after reading it.
    """

    clients_total: int
    clients_blocked: int
    cases_blocked: int
    cases_ready: int
    cases_completed: int
    documents_awaiting_review: int
    exceptions_open: int
    tasks_open: int
    tasks_overdue: int
    calls_required: int
    #: Messages the agent sent today, against the firm's own ceiling.
    messages_sent_today: int
    message_limit_per_day: int
    agent_enabled: bool


class AgentPolicyIn(BaseModel):
    enabled: bool | None = None
    allow_whatsapp: bool | None = None
    allow_voice_calls: bool | None = None
    allow_auto_followup: bool | None = None
    allow_auto_escalation: bool | None = None
    max_messages_per_day: int | None = Field(default=None, ge=0, le=10000)
    max_calls_per_day: int | None = Field(default=None, ge=0, le=1000)
    max_followups_per_case: int | None = Field(default=None, ge=0, le=20)
    first_reminder_hours: int | None = Field(default=None, ge=1, le=720)
    second_reminder_hours: int | None = Field(default=None, ge=1, le=720)
    voice_call_after_hours: int | None = Field(default=None, ge=1, le=720)
    classification_threshold: str | None = None
    local_ai_only: bool | None = None
    allow_ai_planning: bool | None = None
    quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=23)


class AgentPolicyOut(AgentPolicyIn):
    enabled: bool
    allow_whatsapp: bool
    allow_voice_calls: bool
    allow_auto_followup: bool
    allow_auto_escalation: bool
    max_messages_per_day: int
    max_calls_per_day: int
    max_followups_per_case: int
    first_reminder_hours: int
    second_reminder_hours: int
    voice_call_after_hours: int
    classification_threshold: str
    local_ai_only: bool
    allow_ai_planning: bool
    quiet_hours_start: int
    quiet_hours_end: int


class AgentRunIn(BaseModel):
    """Ask the agent to look at everything and chase what needs chasing."""

    case_id: str | None = None
    dry_run: bool = False


class AgentRunOut(BaseModel):
    scheduled: int
    sent: int
    skipped: list[str] = Field(default_factory=list)
