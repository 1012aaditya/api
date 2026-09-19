"""Sending to clients, and receiving what they send back.

Everything that reaches a real person goes through ``send``, and ``send``
refuses before it delivers. The checks are in one place on purpose: a product
that messages people on their own initiative earns trust by being hard to
misuse, and a guard that lives in five call sites is a guard that will
eventually be missed in one (§17, §37).

The refusals, in the order they are checked:

1. **The client asked not to be contacted.** Nothing overrides this.
2. **The firm's policy** — the agent is off, or WhatsApp is off.
3. **The daily cap**, counted from the audit trail rather than a counter,
   because a counter and the events it summarises eventually disagree.
4. **Quiet hours.** Nobody wants a compliance reminder at 2am.

Every refusal is recorded. "We did not message them, and here is why" is
information a CA needs as much as "we did".
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.base import utcnow
from app.models import (
    ActorType,
    AgentPolicy,
    Channel,
    Client,
    ContactState,
    Conversation,
    ConversationStatus,
    Direction,
    Message,
    MessageStatus,
    MessageType,
)
from app.providers.messaging.base import InboundMessage, WhatsAppProvider
from app.providers.messaging.registry import get_provider
from app.repositories.operations import AgentEventRepository, AgentPolicyRepository

logger = get_logger("docuparse.messaging")

#: Sending is counted by this action, so the cap and the timeline agree.
SENT_ACTION = "whatsapp.sent"


@dataclass(frozen=True)
class SendDecision:
    """Whether a message may go out, and the sentence explaining it."""

    allowed: bool
    reason: str | None = None
    code: str | None = None


@dataclass(frozen=True)
class SendAttempt:
    """What came of one attempt to send, and whether trying again could help.

    The follow-up engine needs the difference. "Quiet hours" passes by
    itself; "the client opted out" does not, and a ladder that retried it
    would spend its attempts on a refusal it already understands.
    """

    message: Message | None
    ok: bool
    code: str | None = None
    reason: str | None = None
    #: Seconds to wait before this same rung is worth trying again, or None
    #: when nothing about waiting would change the answer.
    retry_in_seconds: int | None = None


@dataclass(frozen=True)
class TemplateFallback:
    """What to send when a provider will not carry free-form text.

    WhatsApp allows free text only within 24 hours of the client's last
    message, which is exactly the window a reminder falls outside of. Past
    it, the only thing that may be sent is a template the business had
    approved beforehand, with its variables filled positionally — so the
    order of this dict is the order of the template's placeholders.
    """

    name: str
    variables: dict[str, str]

    def describe(self) -> str:
        """What to write on the timeline.

        Not the message text: the wording lives in Meta's approved template,
        and inventing an approximation of it here would put words on the
        firm's timeline that nobody sent (§28).
        """
        values = ", ".join(str(value) for value in self.variables.values())
        return f"(approved template “{self.name}”{f': {values}' if values else ''})"


#: Codes where the obstacle is the clock, not the client or the firm.
_WAITABLE = {"quiet_hours", "daily_cap"}

#: How long to wait on a provider that failed for a reason that may pass.
PROVIDER_RETRY_SECONDS = 900


def _seconds_until_hour(now: dt.datetime, hour: int) -> int:
    """Seconds from ``now`` to the next time it is ``hour``:00."""
    target = now.replace(hour=hour % 24, minute=0, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return max(60, int((target - now).total_seconds()))


def _seconds_until_tomorrow(now: dt.datetime) -> int:
    return max(60, int((_start_of_day(now) + dt.timedelta(days=1) - now).total_seconds()))


def _start_of_day(now: dt.datetime) -> dt.datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def in_quiet_hours(policy: AgentPolicy, now: dt.datetime) -> bool:
    """Whether ``now`` falls inside the firm's do-not-disturb window.

    Handles the window that wraps midnight (21:00 to 09:00), which is the
    normal case and the one an hour comparison gets wrong.
    """
    start, end = policy.quiet_hours_start, policy.quiet_hours_end
    hour = now.hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


class MessagingService:
    """Conversations, outbound sends, and inbound handling."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        settings: Settings | None = None,
        provider: WhatsAppProvider | None = None,
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()

        self._provider = provider
        self._events = AgentEventRepository(db)

    @property
    def provider(self) -> WhatsAppProvider:
        return self._provider or get_provider()

    # -- may we? --------------------------------------------------------

    @property
    def template_name(self) -> str | None:
        """The approved template used to reopen a conversation, if any."""
        return self._settings.whatsapp_template_name

    async def may_send(
        self,
        client: Client,
        *,
        channel: str = Channel.WHATSAPP,
        now: dt.datetime | None = None,
        automated: bool = True,
    ) -> SendDecision:
        """Decide whether an automated message may go to this client.

        ``automated=False`` is a human pressing send in the dashboard. That
        skips the agent's policy and caps — they exist to restrain the agent,
        not the CA — but still respects an explicit opt-out, because the
        client asked the firm, not the software.
        """
        moment = now or utcnow()

        if not client.allow_automated_contact:
            return SendDecision(
                False,
                f"{client.display_name} has asked not to be contacted automatically.",
                "opted_out",
            )
        if not client.contactable_on(channel):
            return SendDecision(
                False,
                f"{client.display_name} has no usable {channel} number.",
                "no_address",
            )

        if not automated:
            return SendDecision(True)

        policy = await AgentPolicyRepository(self._db).get_or_create(
            client.organization_id
        )
        if not policy.enabled:
            return SendDecision(False, "The agent is switched off for this firm.", "agent_off")
        if channel == Channel.WHATSAPP and not policy.allow_whatsapp:
            return SendDecision(False, "WhatsApp is switched off for this firm.", "channel_off")

        sent_today = await self._events.count_since(
            client.organization_id, SENT_ACTION, since=_start_of_day(moment)
        )
        if sent_today >= policy.max_messages_per_day:
            return SendDecision(
                False,
                f"The firm's daily limit of {policy.max_messages_per_day} messages is reached.",
                "daily_cap",
            )

        if in_quiet_hours(policy, moment):
            return SendDecision(
                False,
                f"It is quiet hours ({policy.quiet_hours_start}:00–"
                f"{policy.quiet_hours_end}:00).",
                "quiet_hours",
            )

        return SendDecision(True)

    # -- conversations --------------------------------------------------

    async def conversation_for(
        self, client: Client, *, channel: str = Channel.WHATSAPP
    ) -> Conversation:
        existing = (
            await self._db.execute(
                select(Conversation).where(
                    Conversation.organization_id == client.organization_id,
                    Conversation.client_id == client.id,
                    Conversation.channel == channel,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        conversation = Conversation(
            organization_id=client.organization_id,
            client_id=client.id,
            channel=channel,
            phone=client.whatsapp_phone or client.phone,
        )
        self._db.add(conversation)
        await self._db.flush()
        return conversation

    # -- outbound -------------------------------------------------------

    async def send(
        self,
        client: Client,
        body: str,
        *,
        case_id: str | None = None,
        automated: bool = True,
        actor_type: str = ActorType.AGENT,
        actor_id: str | None = None,
        now: dt.datetime | None = None,
    ) -> Message | None:
        """Send, or record why not. Returns None when refused."""
        attempt = await self.send_with_reason(
            client,
            body,
            case_id=case_id,
            automated=automated,
            actor_type=actor_type,
            actor_id=actor_id,
            now=now,
        )
        return attempt.message

    async def send_with_reason(
        self,
        client: Client,
        body: str,
        *,
        case_id: str | None = None,
        automated: bool = True,
        actor_type: str = ActorType.AGENT,
        actor_id: str | None = None,
        now: dt.datetime | None = None,
        template: TemplateFallback | None = None,
    ) -> SendAttempt:
        """Send, or record why not, and say whether waiting would help.

        The refusal is written to the timeline rather than raised, because
        "we held this back because the client opted out" is a normal outcome
        the firm should see, not an error anybody needs to catch.
        """
        moment = now or utcnow()
        decision = await self.may_send(client, now=moment, automated=automated)
        if not decision.allowed:
            await self._events.record(
                organization_id=client.organization_id,
                client_id=client.id,
                case_id=case_id,
                actor_type=actor_type,
                actor_id=actor_id,
                action="whatsapp.withheld",
                summary=f"Did not message {client.display_name}: {decision.reason}",
                details={"code": decision.code, "reason": decision.reason},
                created_at=now,
            )
            logger.info("whatsapp.withheld", code=decision.code)
            return SendAttempt(
                message=None,
                ok=False,
                code=decision.code,
                reason=decision.reason,
                retry_in_seconds=await self._wait_for(client, decision, now=moment),
            )

        conversation = await self.conversation_for(client)
        destination = client.whatsapp_phone or client.phone or ""
        result = await self.provider.send_message(to=destination, body=body)

        kind = MessageType.TEXT
        if not result.ok and result.details.get("needs_template") and template is not None:
            # The window has closed. The approved template is the only thing
            # that may go out, so the reminder happens — it just has different
            # words, and the timeline says so rather than showing the text
            # that was refused.
            fallback = await self.provider.send_template(
                to=destination, template=template.name, variables=template.variables
            )
            if fallback.ok:
                result = fallback
                kind = MessageType.TEMPLATE
                body = template.describe()

        # One clock for the row, the conversation and the event: taking
        # utcnow() twice would put a message in the timeline at a different
        # moment from the event that records it.
        message = Message(
            organization_id=client.organization_id,
            conversation_id=conversation.id,
            client_id=client.id,
            case_id=case_id,
            direction=Direction.OUTBOUND,
            type=kind,
            body=body,
            provider=getattr(self.provider, "name", None),
            provider_message_id=result.provider_message_id,
            status=MessageStatus.SENT if result.ok else MessageStatus.FAILED,
            error=result.error,
            sent_by_agent=actor_type == ActorType.AGENT,
            created_at=moment,
        )
        self._db.add(message)

        if result.ok:
            conversation.last_message_at = moment
            conversation.status = ConversationStatus.AWAITING_CLIENT
            client.last_contacted_at = moment
            if client.contact_state == ContactState.NOT_CONTACTED:
                client.contact_state = ContactState.CONTACTED

        await self._events.record(
            organization_id=client.organization_id,
            client_id=client.id,
            case_id=case_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=SENT_ACTION if result.ok else "whatsapp.failed",
            summary=(
                f"WhatsApp sent to {client.display_name}"
                if result.ok
                else f"WhatsApp to {client.display_name} failed: {result.error}"
            ),
            entity_type="message",
            entity_id=message.id,
            # The body is the firm's own words to their own client, so it is
            # kept — but only here, never in the application log.
            details={"preview": body[:160], "ok": result.ok},
            created_at=moment,
        )
        await self._db.flush()

        if result.ok:
            return SendAttempt(message=message, ok=True)

        # A provider that said no permanently — the number is not on
        # WhatsApp, the 24-hour window has closed — is not worth retrying;
        # somebody has to change something first.
        permanent = bool(result.details.get("permanent"))
        return SendAttempt(
            message=message,
            ok=False,
            code="provider_permanent" if permanent else "provider_failed",
            reason=result.error,
            retry_in_seconds=None if permanent else PROVIDER_RETRY_SECONDS,
        )

    async def _wait_for(
        self, client: Client, decision: SendDecision, *, now: dt.datetime
    ) -> int | None:
        if decision.code not in _WAITABLE:
            return None
        policy = await AgentPolicyRepository(self._db).get_or_create(
            client.organization_id
        )
        if decision.code == "quiet_hours":
            # Just after the window ends, not in fifteen minutes: three
            # retries through the night would exhaust the job's attempts
            # without a single message going out.
            return _seconds_until_hour(now, policy.quiet_hours_end)
        return _seconds_until_tomorrow(now)

    # -- inbound --------------------------------------------------------

    async def already_handled(self, organization_id: str, provider_message_id: str) -> bool:
        """Whether this provider message has been stored before.

        Providers redeliver. Without this, one client message becomes three
        replies and three follow-up schedules (§18, §23).
        """
        found = (
            await self._db.execute(
                select(Message.id).where(
                    Message.organization_id == organization_id,
                    Message.provider_message_id == provider_message_id,
                )
            )
        ).scalar_one_or_none()
        return found is not None

    async def record_inbound(
        self,
        client: Client,
        inbound: InboundMessage,
        *,
        case_id: str | None = None,
        document_id: str | None = None,
        detected_intent: str | None = None,
        intent_details: dict | None = None,
        now: dt.datetime | None = None,
    ) -> Message:
        conversation = await self.conversation_for(client)
        moment = now or utcnow()

        message = Message(
            organization_id=client.organization_id,
            conversation_id=conversation.id,
            client_id=client.id,
            case_id=case_id,
            direction=Direction.INBOUND,
            type=(
                MessageType.DOCUMENT
                if inbound.type in ("document", "image")
                else MessageType.TEXT
            ),
            body=inbound.body,
            document_id=document_id,
            media_reference=inbound.media_reference,
            provider=getattr(self.provider, "name", None),
            provider_message_id=inbound.provider_message_id,
            status=MessageStatus.RECEIVED,
            detected_intent=detected_intent,
            intent_details=intent_details or {},
            sent_by_agent=False,
            created_at=moment,
        )
        self._db.add(message)

        conversation.last_message_at = moment
        conversation.last_inbound_at = moment
        conversation.status = ConversationStatus.AWAITING_FIRM
        client.last_response_at = moment
        if client.contact_state in (ContactState.CONTACTED, ContactState.FOLLOW_UP_REQUIRED):
            client.contact_state = ContactState.RESPONDED

        await self._events.record(
            organization_id=client.organization_id,
            client_id=client.id,
            case_id=case_id,
            actor_type=ActorType.CLIENT,
            actor_id=client.id,
            action="whatsapp.received",
            summary=(
                f"{client.display_name} sent {inbound.filename}"
                if inbound.filename
                else f"{client.display_name} replied"
            ),
            entity_type="message",
            entity_id=message.id,
            details={
                "type": inbound.type,
                "intent": detected_intent,
                "preview": (inbound.body or "")[:160],
            },
            created_at=moment,
        )
        await self._db.flush()
        return message
