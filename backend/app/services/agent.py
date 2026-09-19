"""The client communication agent.

Small, specialised workflows rather than one autonomous agent (§34). Each one
is ordinary code that decides *what* to do; the only thing a model is trusted
with is wording, and even that has a deterministic fallback so the product
works with no AI configured at all.

Two properties matter more than any feature here:

**The organization is never an argument.** Every tool is bound to an
``AgentContext`` built from an authenticated session. A model cannot name a
different firm's client, because it never supplies an organization id — it
supplies a client id, which is then looked up *within* the bound organization
and comes back None if it belongs to anyone else (§20).

**Every factual claim comes from the database.** The agent may write "we still
need your bank statement" only because a requirement row says so. It has no
way to assert that a document arrived, a call happened, or validation passed
(§28).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.base import utcnow
from app.models import (
    ActorType,
    CaseStatus,
    Client,
    ComplianceCase,
    ContactState,
    DocumentRequirement,
    ExceptionType,
    Priority,
    RequirementStatus,
    Severity,
)
from app.repositories.clients import (
    CaseRepository,
    ClientFactRepository,
    ClientRepository,
    RequirementRepository,
)
from app.repositories.operations import (
    AgentEventRepository,
    AgentPolicyRepository,
    ExceptionRepository,
    TaskRepository,
)
from app.services.intent import Intent, IntentReading
from app.services.messaging import MessagingService, TemplateFallback
from app.utils.ids import prefixed_id

logger = get_logger("docuparse.agent")


@dataclass(frozen=True)
class AgentContext:
    """Who the agent is acting for.

    Built from an authenticated session, never from model output. This is the
    entire authorization story: every tool below reads ``organization_id`` from
    here and from nowhere else.
    """

    organization_id: str
    #: Groups the events of one run so a timeline can be collapsed.
    run_id: str = field(default_factory=lambda: prefixed_id("run"))
    actor_type: str = ActorType.AGENT
    actor_id: str | None = None


@dataclass
class AgentResult:
    """What a workflow did, in terms the dashboard can show."""

    actions: list[str] = field(default_factory=list)
    messages_sent: int = 0
    exceptions_raised: int = 0
    tasks_created: int = 0
    skipped: str | None = None
    #: Set when the thing that stopped this — quiet hours, the daily cap, a
    #: provider that was unreachable — will not be there later. The caller
    #: puts the work back on the queue instead of consuming it.
    retry_in_seconds: int | None = None

    @property
    def did_something(self) -> bool:
        return bool(self.actions)


def describe_missing(requirements: list[DocumentRequirement]) -> str:
    """Name the outstanding documents the way a person would.

    "your September bank statement and credit notes", not
    "['bank_statement', 'credit_note']".
    """
    labels = [r.label.lower() for r in requirements]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


def compose_request(
    *, firm_name: str, case: ComplianceCase, missing: list[DocumentRequirement]
) -> str:
    """Write the message asking for what is missing.

    Deterministic on purpose. Every fact in the sentence — the period, the
    document names — comes from rows, so the message cannot claim the firm
    needs something it does not. A model may later rephrase this; it may not
    decide its contents.
    """
    what = describe_missing(missing)
    period = case.period
    return (
        f"Hi, we're preparing your {case.type.upper()} filing for {period}. "
        f"We still need your {what}. "
        "You can reply to this message with the files."
        f"\n\n— {firm_name}"
    )


class ClientCommunicationAgent:
    """Chases documents, reads replies, and knows when to stop."""

    def __init__(
        self,
        db: AsyncSession,
        context: AgentContext,
        *,
        messaging: MessagingService | None = None,
    ) -> None:
        self._db = db
        self._ctx = context
        self._clients = ClientRepository(db)
        self._cases = CaseRepository(db)
        self._requirements = RequirementRepository(db)
        self._exceptions = ExceptionRepository(db)
        self._tasks = TaskRepository(db)
        self._facts = ClientFactRepository(db)
        self._events = AgentEventRepository(db)
        self._messaging = messaging or MessagingService(db)

    # -- tools ----------------------------------------------------------
    #
    # Each takes ids, never an organization. The lookup is scoped by the bound
    # context, so an id belonging to another firm simply does not resolve.

    async def get_client(self, client_id: str) -> Client | None:
        return await self._clients.get(self._ctx.organization_id, client_id)

    async def get_case(self, case_id: str) -> ComplianceCase | None:
        return await self._cases.get(self._ctx.organization_id, case_id)

    async def get_missing_documents(self, case_id: str) -> list[DocumentRequirement]:
        return await self._requirements.outstanding_for_case(self._ctx.organization_id, case_id)

    async def create_exception(
        self,
        *,
        type: str,
        message: str,
        dedupe_key: str,
        severity: str = Severity.WARNING,
        client_id: str | None = None,
        case_id: str | None = None,
        details: dict | None = None,
    ):
        item = await self._exceptions.raise_exception(
            organization_id=self._ctx.organization_id,
            type=type,
            message=message,
            dedupe_key=dedupe_key,
            severity=severity,
            client_id=client_id,
            case_id=case_id,
            details=details,
        )
        await self._record("exception.raised", message, client_id=client_id, case_id=case_id)
        return item

    async def create_task(
        self,
        *,
        title: str,
        client_id: str | None = None,
        case_id: str | None = None,
        description: str | None = None,
        priority: str = Priority.NORMAL,
        due_at: dt.datetime | None = None,
    ):
        task = await self._tasks.create(
            self._ctx.organization_id,
            client_id=client_id,
            case_id=case_id,
            title=title,
            description=description,
            priority=priority,
            due_at=due_at,
            created_by="agent",
        )
        await self._record("task.created", title, client_id=client_id, case_id=case_id)
        return task

    async def _record(
        self,
        action: str,
        summary: str,
        *,
        client_id: str | None = None,
        case_id: str | None = None,
        details: dict | None = None,
    ) -> None:
        await self._events.record(
            organization_id=self._ctx.organization_id,
            client_id=client_id,
            case_id=case_id,
            actor_type=self._ctx.actor_type,
            actor_id=self._ctx.actor_id,
            action=action,
            summary=summary,
            details=details or {},
            run_id=self._ctx.run_id,
        )

    # -- workflow: ask for what is missing -------------------------------

    def _reminder_template(
        self,
        client: Client,
        case: ComplianceCase,
        missing: list[DocumentRequirement],
        *,
        firm_name: str,
    ) -> TemplateFallback | None:
        """The approved template to fall back on, when the firm has one.

        The variables are positional in WhatsApp, so this order is part of
        the template's definition: firm, client, period, documents.
        """
        name = self._messaging.template_name
        if not name:
            return None
        return TemplateFallback(
            name=name,
            variables={
                "firm": firm_name,
                "client": client.display_name,
                "period": f"{case.type.upper()} {case.period}",
                "documents": describe_missing(missing),
            },
        )

    async def request_missing_documents(
        self,
        case: ComplianceCase,
        *,
        firm_name: str,
        now: dt.datetime | None = None,
        body: str | None = None,
    ) -> AgentResult:
        """The core loop: find what is outstanding, ask for it, record it.

        ``body`` replaces the composed wording — how the planner's message
        gets sent. Everything around it is unchanged on purpose: the same
        policy gate decides whether it may go, and the same rule marks the
        requirements as asked-for only once it has.
        """
        result = AgentResult()

        if case.status == CaseStatus.COMPLETED:
            result.skipped = "The case is already completed."
            return result

        client = await self.get_client(case.client_id)
        if client is None:
            result.skipped = "That client does not belong to this firm."
            return result

        missing = await self.get_missing_documents(case.id)
        if not missing:
            result.skipped = "Nothing is outstanding."
            return result

        policy = await AgentPolicyRepository(self._db).get_or_create(self._ctx.organization_id)
        if not policy.allow_auto_followup:
            result.skipped = "Automatic follow-up is switched off for this firm."
            return result

        body = body or compose_request(firm_name=firm_name, case=case, missing=missing)
        attempt = await self._messaging.send_with_reason(
            client,
            body,
            case_id=case.id,
            actor_type=self._ctx.actor_type,
            actor_id=self._ctx.actor_id,
            now=now,
            template=self._reminder_template(client, case, missing, firm_name=firm_name),
        )
        if not attempt.ok:
            result.skipped = attempt.reason or "The message was not sent."
            result.retry_in_seconds = attempt.retry_in_seconds
            return result

        # The requirements are marked requested only after a send succeeded.
        # Marking them first would be the product telling the CA it asked when
        # it had not (§28).
        for requirement in missing:
            if requirement.status in (
                RequirementStatus.MISSING,
                RequirementStatus.INVALID,
            ):
                requirement.move_to(RequirementStatus.REQUESTED)

        if case.status == CaseStatus.NOT_STARTED:
            case.status = CaseStatus.BLOCKED

        result.messages_sent = 1
        result.actions.append(f"Asked {client.display_name} for {describe_missing(missing)}")
        await self._record(
            "documents.requested",
            f"Asked {client.display_name} for {describe_missing(missing)}",
            client_id=client.id,
            case_id=case.id,
            details={"requested": [r.document_type for r in missing]},
        )
        await self._db.flush()
        return result

    # -- workflow: a client replied --------------------------------------

    async def handle_reply(
        self,
        client: Client,
        reading: IntentReading,
        *,
        case: ComplianceCase | None = None,
        text: str = "",
        now: dt.datetime | None = None,
    ) -> AgentResult:
        """Act on what a client said — without believing it about the world.

        "bhej diya" means the client believes they sent it. It does not mean a
        document arrived, so nothing here settles a requirement. What it does
        is decide whether to wait, to ask again, or to fetch a human.
        """
        result = AgentResult()
        moment = now or utcnow()

        if reading.intent == Intent.DO_NOT_CONTACT:
            client.allow_automated_contact = False
            client.automation_paused_reason = "The client asked not to be contacted."
            client.contact_state = ContactState.OPTED_OUT
            await self.create_exception(
                type=ExceptionType.CLIENT_OPTED_OUT,
                severity=Severity.HIGH,
                message=(
                    f"{client.display_name} asked not to be contacted. Automated "
                    "messages are off for them; someone should decide how to proceed."
                ),
                dedupe_key=f"opted_out:{client.id}",
                client_id=client.id,
                case_id=case.id if case else None,
                details={"said": text[:200]},
            )
            result.exceptions_raised += 1
            result.actions.append(f"Stopped contacting {client.display_name}")
            await self._db.flush()
            return result

        if reading.intent == Intent.WRONG_NUMBER:
            client.allow_automated_contact = False
            client.automation_paused_reason = "The number may belong to someone else."
            await self.create_exception(
                type=ExceptionType.WRONG_NUMBER,
                severity=Severity.HIGH,
                message=(
                    f"The number on file for {client.display_name} may be wrong — "
                    "whoever replied said so. Automated contact is paused."
                ),
                dedupe_key=f"wrong_number:{client.id}",
                client_id=client.id,
                details={"said": text[:200]},
            )
            result.exceptions_raised += 1
            result.actions.append("Paused contact: the number may be wrong")
            await self._db.flush()
            return result

        if reading.intent == Intent.WANTS_HUMAN:
            client.contact_state = ContactState.ESCALATED
            await self.create_task(
                title=f"Call {client.display_name} — they asked to speak to someone",
                client_id=client.id,
                case_id=case.id if case else None,
                description=text[:500] or None,
                priority=Priority.HIGH,
                due_at=moment,
            )
            result.tasks_created += 1
            result.actions.append(f"{client.display_name} asked for a person")
            await self._db.flush()
            return result

        if reading.intent == Intent.DOCUMENT_COMMITMENT:
            client.contact_state = ContactState.COMMITTED
            when = reading.commitment_date
            await self._facts.record(
                organization_id=self._ctx.organization_id,
                client_id=client.id,
                key="document_commitment_date",
                value={
                    "date": when.isoformat() if when else None,
                    "said": text[:200],
                    "case_id": case.id if case else None,
                },
                source="agent",
                confidence=str(reading.confidence),
                # A promise has a shelf life. Without one it sits in the
                # client's record for ever and every later decision is still
                # being made on the strength of something they said in
                # September — which is how one "kal bhej dunga" stalls a chase
                # indefinitely.
                expires_at=(
                    dt.datetime.combine(when, dt.time(23, 59), tzinfo=dt.UTC)
                    + dt.timedelta(days=2)
                    if when
                    else (now or utcnow()) + dt.timedelta(days=3)
                ),
            )
            said = when.strftime("%d %b") if when else "soon"
            result.actions.append(f"{client.display_name} said they would send it by {said}")
            await self._record(
                "client.committed",
                f"{client.display_name} said they would send the documents by {said}",
                client_id=client.id,
                case_id=case.id if case else None,
                details={"date": when.isoformat() if when else None},
            )
            await self._db.flush()
            return result

        if reading.intent == Intent.DOCUMENT_NOT_AVAILABLE:
            await self.create_task(
                title=f"{client.display_name} says they do not have the documents",
                client_id=client.id,
                case_id=case.id if case else None,
                description=text[:500] or None,
                priority=Priority.HIGH,
            )
            result.tasks_created += 1
            result.actions.append("Flagged: the client says the documents do not exist")
            await self._db.flush()
            return result

        if reading.intent in (Intent.CLARIFICATION_REQUIRED, Intent.DOCUMENT_QUESTION):
            if case is not None:
                missing = await self.get_missing_documents(case.id)
                if missing:
                    body = (
                        f"For {case.type.upper()} {case.period} we still need your "
                        f"{describe_missing(missing)}. Send whichever you have and "
                        "we'll take it from there."
                    )
                    # `.ok`, not "a row came back": a failed send still
                    # writes a message row, and counting that as an answer
                    # would tell the CA their client was told something they
                    # were not.
                    attempt = await self._messaging.send_with_reason(
                        client,
                        body,
                        case_id=case.id,
                        actor_type=self._ctx.actor_type,
                        actor_id=self._ctx.actor_id,
                        now=now,
                    )
                    if attempt.ok:
                        result.messages_sent += 1
                        result.actions.append("Explained what is still needed")
                        await self._db.flush()
                        return result
            # No case, or nothing to tell them: a person should answer.
            await self.create_task(
                title=f"{client.display_name} asked a question",
                client_id=client.id,
                case_id=case.id if case else None,
                description=text[:500] or None,
            )
            result.tasks_created += 1
            result.actions.append("Passed the question to a person")
            await self._db.flush()
            return result

        if reading.intent == Intent.DOCUMENT_SENT:
            # They believe they sent it; nothing has arrived. Say so plainly
            # rather than marking anything received.
            result.actions.append(
                f"{client.display_name} says they sent it; nothing has arrived yet"
            )
            await self._record(
                "client.claims_sent",
                f"{client.display_name} says the documents were sent. "
                "Nothing has been received against this case.",
                client_id=client.id,
                case_id=case.id if case else None,
            )
            await self._db.flush()
            return result

        result.skipped = "Nothing in the reply needed an action."
        return result
