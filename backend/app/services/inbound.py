"""Handling one message a client sent us.

Called by the webhook route and by the demo simulator, so the two cannot
drift: whatever the demo demonstrates is the code production runs.

The order matters and is not arbitrary:

1. **Have we seen this message id before?** Providers redeliver, and doing the
   work twice means two replies and two follow-up schedules (§18).
2. **Whose number is this?** An unrecognised number is filed for a human, not
   guessed at.
3. **Store first, interpret second.** A document is saved and recorded before
   anything tries to understand it, so a crash in classification never loses
   a client's file.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import DocuParseError
from app.core.logging import get_logger
from app.models import (
    CaseStatus,
    Client,
    ComplianceCase,
    Document,
    DocumentType,
    Message,
    Severity,
)
from app.providers.messaging.base import InboundMessage
from app.repositories.clients import CaseRepository, ClientRepository
from app.repositories.jobs import JobRepository
from app.repositories.operations import AgentEventRepository, ExceptionRepository
from app.services.agent import AgentContext, ClientCommunicationAgent
from app.services.file_validation import validate_upload
from app.services.ingestion import IngestionService, refresh_case_status
from app.services.intent import read_intent
from app.services.messaging import MessagingService
from app.services.storage import build_storage_key, get_object_store

logger = get_logger("docuparse.inbound")

#: Cases the agent will attach an incoming document to, newest deadline first.
_OPEN_CASE_STATUSES = (
    CaseStatus.BLOCKED,
    CaseStatus.IN_PROGRESS,
    CaseStatus.NOT_STARTED,
    CaseStatus.ESCALATED,
)


@dataclass
class InboundOutcome:
    handled: bool
    reason: str | None = None
    message: Message | None = None
    document: Document | None = None
    actions: list[str] | None = None


class InboundService:
    def __init__(self, db: AsyncSession, *, settings: Settings | None = None) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._messaging = MessagingService(db, settings=self._settings)
        self._events = AgentEventRepository(db)
        self._exceptions = ExceptionRepository(db)

    async def _open_case(self, client: Client) -> ComplianceCase | None:
        """The case an unattributed document most likely belongs to.

        The earliest deadline among the client's open cases. A guess, and
        treated as one: the document is attached, and if it does not fit the
        case's requirements the ingestion layer files an exception rather than
        forcing it.
        """
        cases = await CaseRepository(self._db).list_for_organization(
            client.organization_id, client_id=client.id
        )
        open_cases = [case for case in cases if case.status in _OPEN_CASE_STATUSES]
        return open_cases[0] if open_cases else None

    async def handle(
        self,
        *,
        organization_id: str,
        inbound: InboundMessage,
        firm_name: str = "your CA",
        now: dt.datetime | None = None,
    ) -> InboundOutcome:
        if await self._messaging.already_handled(
            organization_id, inbound.provider_message_id
        ):
            return InboundOutcome(handled=False, reason="already_processed")

        client = await ClientRepository(self._db).find_by_phone(
            organization_id, inbound.from_phone
        )
        if client is None:
            # Not an error — someone messaged the firm's number. File it so a
            # person can decide, and do not reply to a stranger.
            await self._exceptions.raise_exception(
                organization_id=organization_id,
                type="unknown_sender",
                severity=Severity.INFO,
                message=(
                    f"A message arrived from {inbound.from_phone}, which does not "
                    "match any client on file."
                ),
                dedupe_key=f"unknown_sender:{inbound.from_phone}",
                details={"preview": (inbound.body or "")[:160]},
            )
            await self._db.flush()
            return InboundOutcome(handled=False, reason="unknown_sender")

        if inbound.type in ("document", "image"):
            return await self._handle_document(client, inbound, now=now)
        return await self._handle_text(client, inbound, firm_name=firm_name, now=now)

    # -- a client sent a file -------------------------------------------

    async def _handle_document(
        self, client: Client, inbound: InboundMessage, *, now: dt.datetime | None
    ) -> InboundOutcome:
        provider = self._messaging.provider
        try:
            content = await provider.fetch_media(inbound.media_reference or "")
        except Exception as exc:  # the provider failed us, not the client
            logger.warning("inbound.media_fetch_failed", error=str(exc))
            await self._exceptions.raise_exception(
                organization_id=client.organization_id,
                client_id=client.id,
                type="unreadable_document",
                severity=Severity.WARNING,
                message=(
                    f"{client.display_name} sent a file we could not download. "
                    "Ask them to send it again."
                ),
                dedupe_key=f"media_fetch:{inbound.provider_message_id}",
                details={"filename": inbound.filename},
            )
            await self._db.flush()
            return InboundOutcome(handled=False, reason="media_unavailable")

        filename = inbound.filename or "whatsapp-upload"
        try:
            validated = validate_upload(
                content,
                filename=filename,
                max_size_bytes=self._settings.max_file_size_bytes,
                max_page_count=self._settings.max_page_count,
            )
        except DocuParseError as exc:
            # A refused file is reported to the CA in the client's own terms,
            # never silently dropped.
            await self._exceptions.raise_exception(
                organization_id=client.organization_id,
                client_id=client.id,
                type="unreadable_document",
                severity=Severity.WARNING,
                message=(
                    f"{client.display_name} sent {filename}, which we cannot read: "
                    f"{exc.message}"
                ),
                dedupe_key=f"rejected_upload:{inbound.provider_message_id}",
                details={"filename": filename, "code": exc.code},
            )
            await self._messaging.record_inbound(client, inbound, now=now)
            await self._db.flush()
            return InboundOutcome(handled=True, reason="rejected_file")

        case = await self._open_case(client)
        document = Document(
            organization_id=client.organization_id,
            client_id=client.id,
            case_id=case.id if case else None,
            filename=validated.filename,
            content_type=validated.content_type,
            size_bytes=validated.size_bytes,
            page_count=validated.page_count,
            checksum_sha256=validated.checksum_sha256,
            source="whatsapp",
        )
        self._db.add(document)
        await self._db.flush()

        key = build_storage_key(
            organization_id=client.organization_id,
            document_id=document.id,
            content_type=validated.content_type,
            now=now or dt.datetime.now(dt.UTC),
        )
        await get_object_store().put(key, content, content_type=validated.content_type)
        document.storage_key = key

        message = await self._messaging.record_inbound(
            client,
            inbound,
            case_id=case.id if case else None,
            document_id=document.id,
            now=now,
        )

        from app.pipelines.stages.preprocess import prepare_document

        prepared = prepare_document(validated)
        outcome = await IngestionService(self._db, settings=self._settings).ingest(
            document=document,
            file=validated,
            prepared=prepared,
            client=client,
            case=case,
        )
        # An invoice has to be read before anything says its requirement is
        # met, and reading it is the extraction pipeline's job. Queue it here,
        # where the document arrived, rather than leaving the two halves of
        # this product unaware of each other.
        if (
            document.classified_type in DocumentType.INVOICE_LIKE
            and outcome.requirement is not None
        ):
            await JobRepository(self._db).create(
                organization_id=client.organization_id,
                document_id=document.id,
                request_id=None,
                document_type=document.classified_type,
            )
            outcome.steps.append("Queued for reading")

        if case is not None:
            await refresh_case_status(self._db, case)

        await self._db.flush()
        return InboundOutcome(
            handled=True,
            message=message,
            document=document,
            actions=outcome.steps,
        )

    # -- a client wrote something ---------------------------------------

    async def _handle_text(
        self,
        client: Client,
        inbound: InboundMessage,
        *,
        firm_name: str,
        now: dt.datetime | None,
    ) -> InboundOutcome:
        today = (now or dt.datetime.now(dt.UTC)).date()
        reading = read_intent(inbound.body or "", today=today)
        case = await self._open_case(client)

        await self._messaging.record_inbound(
            client,
            inbound,
            case_id=case.id if case else None,
            detected_intent=reading.intent,
            intent_details={
                "confidence": reading.confidence,
                "matched": list(reading.matched),
                "commitment_date": (
                    reading.commitment_date.isoformat()
                    if reading.commitment_date
                    else None
                ),
            },
            now=now,
        )

        agent = ClientCommunicationAgent(
            self._db, AgentContext(organization_id=client.organization_id)
        )
        result = await agent.handle_reply(
            client, reading, case=case, text=inbound.body or "", now=now
        )
        await self._db.flush()
        return InboundOutcome(handled=True, actions=result.actions)
