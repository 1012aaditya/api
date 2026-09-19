"""Placing a call, and acting on what was said.

The call itself belongs to a provider. What belongs here is everything around
it: whether the firm allows calls at all, what the agent is allowed to say,
and — the part that matters — what a recorded intent may and may not change.

The rule is the same one that governs WhatsApp replies (§28): hearing "I sent
it yesterday" changes no requirement. A call can record a commitment, stop
automated contact, or fetch a human. It cannot make a document exist.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.base import utcnow
from app.models import (
    ActorType,
    Call,
    CallIntent,
    CallStatus,
    Channel,
    Client,
    ComplianceCase,
)
from app.providers.messaging.voice import get_provider
from app.repositories.agent_jobs import AgentJobRepository
from app.repositories.clients import RequirementRepository
from app.repositories.operations import AgentEventRepository, AgentPolicyRepository
from app.repositories.organizations import OrganizationRepository
from app.services.agent import AgentContext, ClientCommunicationAgent, describe_missing
from app.services.intent import Intent, read_intent

logger = get_logger("docuparse.voice.engine")

#: How a recorded call intent maps onto the intent vocabulary the agent
#: already knows, so one set of rules governs both channels.
_CALL_TO_REPLY = {
    CallIntent.DOCUMENT_SENT: Intent.DOCUMENT_SENT,
    CallIntent.DOCUMENT_COMMITMENT: Intent.DOCUMENT_COMMITMENT,
    CallIntent.DOCUMENT_NOT_AVAILABLE: Intent.DOCUMENT_NOT_AVAILABLE,
    CallIntent.CLIENT_CONFUSED: Intent.CLARIFICATION_REQUIRED,
    CallIntent.CLIENT_WANTS_HUMAN: Intent.WANTS_HUMAN,
    CallIntent.WRONG_NUMBER: Intent.WRONG_NUMBER,
    CallIntent.DO_NOT_CONTACT: Intent.DO_NOT_CONTACT,
}


def compose_script(
    *, firm_name: str, case: ComplianceCase, missing: list, client: Client
) -> str:
    """What the agent says when the call connects.

    It identifies itself as an assistant in the first sentence. A client who
    thinks they are talking to their accountant and later learns otherwise is
    a client the firm has lost, and no amount of efficiency is worth that.
    """
    return (
        f"Hello, this is the AI assistant calling on behalf of {firm_name}. "
        f"We're preparing your {case.type.upper()} filing for {case.period}, "
        f"and we're still waiting for your {describe_missing(missing)}. "
        "Would you be able to send those over, or would you prefer to speak "
        "to someone at the firm?"
    )


class VoiceEngine:
    def __init__(self, db: AsyncSession, *, settings: Settings | None = None) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._events = AgentEventRepository(db)

    async def place_call(
        self,
        *,
        case: ComplianceCase,
        client: Client,
        now: dt.datetime | None = None,
    ) -> Call | None:
        """Call, if the firm allows it and the client has not opted out."""
        moment = now or utcnow()
        policy = await AgentPolicyRepository(self._db).get_or_create(
            case.organization_id
        )

        if not policy.enabled or not policy.allow_voice_calls:
            return None
        if not client.allow_automated_contact:
            return None
        if not client.contactable_on(Channel.VOICE):
            return None

        placed_today = await self._events.count_since(
            case.organization_id,
            "voice.placed",
            since=moment.replace(hour=0, minute=0, second=0, microsecond=0),
        )
        if placed_today >= policy.max_calls_per_day:
            await self._events.record(
                organization_id=case.organization_id,
                client_id=client.id,
                case_id=case.id,
                action="voice.withheld",
                summary=(
                    f"Did not call {client.display_name}: the firm's daily limit "
                    f"of {policy.max_calls_per_day} calls is reached."
                ),
                created_at=moment,
            )
            return None

        missing = await RequirementRepository(self._db).outstanding_for_case(
            case.organization_id, case.id
        )
        organization = await OrganizationRepository(self._db).get(case.organization_id)
        script = compose_script(
            firm_name=organization.name if organization else "your CA",
            case=case,
            missing=missing,
            client=client,
        )

        destination = client.phone or client.whatsapp_phone or ""
        result = await get_provider().place_call(
            to=destination,
            script=script,
            context={"case_id": case.id, "client_id": client.id},
        )

        call = Call(
            organization_id=case.organization_id,
            client_id=client.id,
            case_id=case.id,
            phone=destination,
            status=CallStatus.DIALING if result.ok else CallStatus.FAILED,
            provider=getattr(get_provider(), "name", None),
            provider_call_id=result.provider_call_id,
            started_at=moment if result.ok else None,
        )
        self._db.add(call)

        await self._events.record(
            organization_id=case.organization_id,
            client_id=client.id,
            case_id=case.id,
            actor_type=ActorType.AGENT,
            action="voice.placed" if result.ok else "voice.failed",
            summary=(
                f"Called {client.display_name}"
                if result.ok
                else f"Could not call {client.display_name}: {result.error}"
            ),
            entity_type="call",
            entity_id=call.id,
            details={"ok": result.ok},
            created_at=moment,
        )
        await self._db.flush()
        return call if result.ok else None

    async def record_outcome(
        self,
        call: Call,
        *,
        status: str,
        transcript: str | None = None,
        duration_seconds: int | None = None,
        intent: str | None = None,
        now: dt.datetime | None = None,
    ) -> Call:
        """Store how a call went, and act on what was said.

        ``intent`` may be supplied by a provider that classifies its own calls;
        otherwise the transcript is read with the same rules used for WhatsApp,
        so both channels are governed by one set of behaviours.
        """
        moment = now or utcnow()
        call.status = status
        call.duration_seconds = duration_seconds
        call.transcript = transcript
        call.ended_at = moment

        client = await self._db.get(Client, call.client_id)
        case = await self._db.get(ComplianceCase, call.case_id) if call.case_id else None

        reading = None
        if intent and intent in _CALL_TO_REPLY:
            call.detected_intent = intent
            mapped = _CALL_TO_REPLY[intent]
            reading = read_intent("", today=moment.date())
            reading = type(reading)(
                intent=mapped,
                confidence=0.9,
                matched=("reported by the voice provider",),
                commitment_date=None,
                source="voice_provider",
            )
        elif transcript:
            reading = read_intent(transcript, today=moment.date())
            call.detected_intent = reading.intent

        call.intent_details = {
            "confidence": reading.confidence if reading else None,
            "matched": list(reading.matched) if reading else [],
            "commitment_date": (
                reading.commitment_date.isoformat()
                if reading and reading.commitment_date
                else None
            ),
        }

        await self._events.record(
            organization_id=call.organization_id,
            client_id=call.client_id,
            case_id=call.case_id,
            actor_type=ActorType.CLIENT,
            action="voice.completed",
            summary=(
                f"Call with {client.display_name if client else 'the client'} "
                f"ended ({status})"
            ),
            entity_type="call",
            entity_id=call.id,
            details={"status": status, "intent": call.detected_intent},
            created_at=moment,
        )

        if status == CallStatus.NO_ANSWER:
            await self._db.flush()
            return call

        if client is not None and reading is not None:
            agent = ClientCommunicationAgent(
                self._db, AgentContext(organization_id=call.organization_id)
            )
            await agent.handle_reply(
                client, reading, case=case, text=transcript or "", now=moment
            )
            if reading.escalates:
                await AgentJobRepository(self._db).cancel_for_client(
                    call.organization_id,
                    client.id,
                    reason=f"the client said {reading.intent} on a call",
                )

        await self._db.flush()
        return call
