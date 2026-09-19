#!/usr/bin/env python
"""Seed a demonstrable firm.

    python scripts/seed_demo.py
    python scripts/seed_demo.py --reset   # tear the firm down and build it again

Creates one CA firm with ten clients in the states a real practice is
actually in on any given day: some blocked, some chased, one that opted out,
one with a GSTIN mismatch waiting for a human, one finished.

Everything here is synthetic (§32). The GSTINs are constructed to pass the
checksum and identify no registered taxpayer; the names are invented; no real
client document is in this repository or produced by this script.

It runs with no external credentials. The WhatsApp provider is the mock, so
the conversations are real rows and no message reaches anybody.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import delete  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.base import utcnow  # noqa: E402
from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models import (  # noqa: E402
    ActorType,
    Base,
    CaseStatus,
    ContactState,
    Document,
    DocumentType,
    ExceptionType,
    Priority,
    RequirementStatus,
    Severity,
)
from app.providers.messaging.base import InboundMessage  # noqa: E402
from app.providers.messaging.mock import MockWhatsAppProvider  # noqa: E402
from app.providers.messaging.registry import set_provider  # noqa: E402
from app.repositories.clients import (  # noqa: E402
    CaseRepository,
    ClientFactRepository,
    ClientRepository,
    RequirementRepository,
)
from app.repositories.operations import (  # noqa: E402
    AgentEventRepository,
    AgentPolicyRepository,
    ExceptionRepository,
    TaskRepository,
)
from app.repositories.organizations import OrganizationRepository  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.services.followup import FollowUpEngine  # noqa: E402
from app.services.intent import Intent  # noqa: E402
from app.services.messaging import MessagingService  # noqa: E402

FIRM = "Sharma & Associates"
EMAIL = "demo@sharma-associates.example"
PASSWORD = "correct-horse-battery-staple"
PERIOD = "2026-09"
DEADLINE = dt.date(2026, 10, 20)

# Checksum-valid and deliberately fictional.
CLIENTS = [
    ("ABC Traders", "ABC Traders Pvt Ltd", "29AABCU9603R1ZJ", "+919800000001"),
    ("Marigold Retail", "Marigold Retail Pvt Ltd", "27AAACA1111A1Z5", "+919800000002"),
    ("Udupi Software", "Udupi Software Systems", "29AABCU9603R1ZJ", "+919800000003"),
    ("Kanchan Textiles", "Kanchan Textiles LLP", "27AAACB2222B1Z3", "+919800000004"),
    ("Deshmukh Motors", "Deshmukh Motors", "27AAACA1111A1Z5", "+919800000005"),
    ("Nandi Foods", "Nandi Foods Pvt Ltd", "29AABCU9603R1ZJ", "+919800000006"),
    ("Pinnacle Logistics", "Pinnacle Logistics", "27AAACB2222B1Z3", "+919800000007"),
    ("Sunrise Chemicals", "Sunrise Chemicals Pvt Ltd", "29AABCU9603R1ZJ", "+919800000008"),
    ("Verma Hardware", "Verma Hardware Stores", "27AAACA1111A1Z5", "+919800000009"),
    ("Coastal Exports", "Coastal Exports LLP", "29AABCU9603R1ZJ", "+919800000010"),
]


async def tear_down(session, organization_id: str) -> None:
    """Delete everything belonging to one organization.

    Walks the metadata rather than naming twelve tables, so a table added
    later is cleared too instead of holding a foreign key that makes the
    next reset fail.
    """
    for table in reversed(Base.metadata.sorted_tables):
        column = table.columns.get("organization_id")
        if column is not None:
            await session.execute(delete(table).where(column == organization_id))
    await session.execute(
        delete(Base.metadata.tables["organizations"]).where(
            Base.metadata.tables["organizations"].c.id == organization_id
        )
    )
    await session.commit()


class RefusedInProduction(RuntimeError):
    pass


async def seed(*, reset: bool = False) -> None:
    settings = get_settings()
    if settings.is_production:
        # Ten invented clients and a month of invented history, written into
        # a real firm's database, would be indistinguishable from their own
        # records a week later. --reset would then delete the real ones.
        raise RefusedInProduction(
            "This seeds invented clients and invented history. It will not run "
            "against APP_ENV=production."
        )
    set_provider(MockWhatsAppProvider())
    now = utcnow()

    async with get_session_factory()() as session:
        organizations = OrganizationRepository(session)
        existing = await organizations.get_by_slug("sharma-associates")
        if existing is not None and not reset:
            print(
                f"The demo firm already exists ({existing.id}). Nothing to do.\n"
                "Pass --reset to tear it down and build it again."
            )
            return
        if existing is not None:
            await tear_down(session, existing.id)
            print(f"Removed the previous demo firm ({existing.id}).")

        organization = await organizations.create(name=FIRM)
        await UserRepository(session).create(
            organization_id=organization.id,
            email=EMAIL,
            password_hash=hash_password(PASSWORD),
            full_name="Anita Sharma",
        )
        # Voice stays off, as it is for every new firm (§17). A demo that
        # ships with calls enabled implies a configured voice provider, and
        # there is none — the CA turns it on themselves once there is.
        await AgentPolicyRepository(session).update(
            organization.id,
            {"max_messages_per_day": 200},
        )
        await session.commit()

        clients = ClientRepository(session)
        cases = CaseRepository(session)
        exceptions = ExceptionRepository(session)
        tasks = TaskRepository(session)
        events = AgentEventRepository(session)
        messaging = MessagingService(session, settings=settings)

        created = []
        for index, (name, business, gstin, phone) in enumerate(CLIENTS):
            client = await clients.create(
                organization.id,
                name=name,
                business_name=business,
                client_code=f"C{index + 1:03d}",
                gstin=gstin,
                phone=phone,
                whatsapp_phone=phone,
                preferred_language="hinglish" if index % 3 == 0 else "en",
            )
            case = await cases.create(
                organization_id=organization.id,
                client_id=client.id,
                case_type="gst",
                period=PERIOD,
                deadline=DEADLINE,
            )
            case.status = CaseStatus.BLOCKED
            created.append((client, case))
        await session.commit()

        requirements = RequirementRepository(session)

        async def request(case_id: str) -> None:
            """Mark what the client still owes as asked for."""
            for requirement in await requirements.outstanding_for_case(
                organization.id, case_id
            ):
                requirement.move_to(RequirementStatus.REQUESTED)

        async def settle(case_id: str, types: tuple[str, ...]) -> None:
            for requirement in await requirements.for_case(organization.id, case_id):
                if requirement.document_type in types:
                    requirement.move_to(RequirementStatus.RECEIVED)
                    requirement.move_to(RequirementStatus.PROCESSING)
                    requirement.move_to(RequirementStatus.VALID)

        # --- two are finished ------------------------------------------
        for client, case in created[:2]:
            await settle(
                case.id,
                (
                    DocumentType.SALES_INVOICE,
                    DocumentType.PURCHASE_INVOICE,
                    DocumentType.BANK_STATEMENT,
                    DocumentType.GSTR_2B,
                ),
            )
            case.status = CaseStatus.READY
            await events.record(
                organization_id=organization.id,
                client_id=client.id,
                case_id=case.id,
                action="case.ready",
                summary=f"{case.label} is ready to file for {client.display_name}",
                created_at=now - dt.timedelta(hours=3),
            )

        # --- three are blocked and have been chased once ---------------
        for client, case in created[2:5]:
            await settle(case.id, (DocumentType.SALES_INVOICE, DocumentType.PURCHASE_INVOICE))
            await messaging.send(
                client,
                f"Hi, we're preparing your GST filing for {PERIOD}. We still need your "
                f"bank statement and gstr-2b. You can reply to this message with the "
                f"files.\n\n— {FIRM}",
                case_id=case.id,
                now=now - dt.timedelta(days=1),
            )
            await request(case.id)
            await FollowUpEngine(session, settings=settings).start_chasing(
                case, now=now - dt.timedelta(days=1)
            )

        # --- one promised to send tomorrow -----------------------------
        client, case = created[5]
        await settle(case.id, (DocumentType.SALES_INVOICE,))
        await messaging.send(
            client,
            "Hi, we still need your bank statement for September.",
            case_id=case.id,
            now=now - dt.timedelta(days=2),
        )
        await request(case.id)
        client.contact_state = ContactState.COMMITTED
        await ClientFactRepository(session).record(
            organization_id=organization.id,
            client_id=client.id,
            key="document_commitment_date",
            value={
                "date": (now + dt.timedelta(days=1)).date().isoformat(),
                "said": "kal bhej dunga",
            },
            source="agent",
            confidence="0.85",
        )
        await events.record(
            organization_id=organization.id,
            client_id=client.id,
            case_id=case.id,
            actor_type=ActorType.CLIENT,
            action="client.committed",
            summary=f"{client.display_name} said they would send the documents tomorrow",
            created_at=now - dt.timedelta(hours=20),
        )

        # --- one has a GSTIN mismatch waiting for a human --------------
        # The story this row tells has to have happened: the firm asked, the
        # client replied with a file, the file did not match them (§28).
        client, case = created[6]
        await settle(case.id, (DocumentType.SALES_INVOICE,))
        await messaging.send(
            client,
            f"Hi, we're preparing your GST filing for {PERIOD}. Please send your "
            "purchase invoices, bank statement and GSTR-2B.",
            case_id=case.id,
            now=now - dt.timedelta(days=3),
        )
        await request(case.id)
        document = Document(
            organization_id=organization.id,
            client_id=client.id,
            case_id=case.id,
            filename="september-purchase-invoice.pdf",
            content_type="application/pdf",
            size_bytes=48_213,
            page_count=1,
            checksum_sha256="d" * 64,
            classified_type=DocumentType.PURCHASE_INVOICE,
            classification_confidence="0.94",
            source="whatsapp",
            status="needs_review",
        )
        session.add(document)
        await session.flush()
        await messaging.record_inbound(
            client,
            InboundMessage(
                provider_message_id="demo-inbound-mismatch",
                from_phone=client.whatsapp_phone or "",
                type="document",
                filename=document.filename,
                media_reference="demo-media/september-purchase-invoice.pdf",
            ),
            case_id=case.id,
            document_id=document.id,
            detected_intent=Intent.DOCUMENT_SENT,
            now=now - dt.timedelta(hours=9),
        )
        received = await requirements.match_document_type(
            organization.id, case.id, DocumentType.PURCHASE_INVOICE
        )
        if received is not None:
            received.move_to(RequirementStatus.RECEIVED)
            received.received_document_id = document.id
            received.move_to(RequirementStatus.NEEDS_REVIEW)
        await exceptions.raise_exception(
            organization_id=organization.id,
            client_id=client.id,
            case_id=case.id,
            document_id=document.id,
            type=ExceptionType.GSTIN_MISMATCH,
            severity=Severity.HIGH,
            message=(
                f"Neither GSTIN on september-purchase-invoice.pdf belongs to "
                f"{client.display_name}. This may be another client's document."
            ),
            dedupe_key=f"gstin_mismatch:{document.id}",
            details={
                "client_gstin": client.gstin,
                "supplier_gstin": "24AAACA9999A1Z1",
                "buyer_gstin": "24AAACB8888B1Z2",
            },
        )

        # --- one opted out ---------------------------------------------
        # Contacted once, asked us to stop, and not contacted since. The
        # opt-out is set after the send, in that order, because the send would
        # be refused otherwise — which is the whole point of the flag.
        client, case = created[7]
        await messaging.send(
            client,
            f"Hi, we're preparing your GST filing for {PERIOD}. Please send your "
            "sales and purchase invoices for the month.",
            case_id=case.id,
            now=now - dt.timedelta(days=4),
        )
        await request(case.id)
        await messaging.record_inbound(
            client,
            InboundMessage(
                provider_message_id="demo-inbound-optout",
                from_phone=client.whatsapp_phone or "",
                body="please do not message me here, I will drop everything at the office",
            ),
            case_id=case.id,
            detected_intent=Intent.DO_NOT_CONTACT,
            now=now - dt.timedelta(days=4) + dt.timedelta(minutes=18),
        )
        client.allow_automated_contact = False
        client.automation_paused_reason = "The client asked not to be contacted."
        client.contact_state = ContactState.OPTED_OUT
        await exceptions.raise_exception(
            organization_id=organization.id,
            client_id=client.id,
            case_id=case.id,
            type=ExceptionType.CLIENT_OPTED_OUT,
            severity=Severity.HIGH,
            message=(
                f"{client.display_name} asked not to be contacted. Automated messages "
                "are off for them; someone should decide how to proceed."
            ),
            dedupe_key=f"opted_out:{client.id}",
        )

        # --- one has exhausted the ladder ------------------------------
        # Three real messages on three different days, so the exception's
        # "after 3 reminders" is something the timeline can show.
        client, case = created[8]
        await settle(case.id, (DocumentType.SALES_INVOICE, DocumentType.PURCHASE_INVOICE))
        for days_ago, body in (
            (9, f"Hi, we need your bank statement and GSTR-2B for {PERIOD} to file your return."),
            (6, "A reminder: your bank statement and GSTR-2B for September are still pending."),
            (3, f"We file on {DEADLINE:%d %b}. Without the bank statement and GSTR-2B we cannot."),
        ):
            await messaging.send(
                client, body, case_id=case.id, now=now - dt.timedelta(days=days_ago)
            )
        await request(case.id)
        client.contact_state = ContactState.ESCALATED
        case.status = CaseStatus.ESCALATED
        await exceptions.raise_exception(
            organization_id=organization.id,
            client_id=client.id,
            case_id=case.id,
            type=ExceptionType.MAX_FOLLOWUPS_REACHED,
            severity=Severity.HIGH,
            message=(
                f"{client.display_name} has not sent bank statement and gstr-2b after "
                "3 reminders. Someone should call them."
            ),
            dedupe_key=f"max_followups:{case.id}",
        )
        await tasks.create(
            organization.id,
            client_id=client.id,
            case_id=case.id,
            title=f"Call {client.display_name} — bank statement and gstr-2b still missing",
            priority=Priority.HIGH,
            due_at=now - dt.timedelta(hours=6),
            created_by="agent",
        )

        # --- one asked a question --------------------------------------
        # The agent does not answer questions about a client's own paperwork.
        # It hands them to a person, which is what this row is for (§16).
        client, case = created[9]
        await settle(
            case.id,
            (
                DocumentType.SALES_INVOICE,
                DocumentType.PURCHASE_INVOICE,
                DocumentType.BANK_STATEMENT,
            ),
        )
        await messaging.send(
            client,
            "Hi, we still need your GSTR-2B for September. You can download it from "
            "the GST portal and send it here.",
            case_id=case.id,
            now=now - dt.timedelta(hours=5),
        )
        await request(case.id)
        question = "mujhe samajh nahi aa raha kaunsa document chahiye"
        await messaging.record_inbound(
            client,
            InboundMessage(
                provider_message_id="demo-inbound-question",
                from_phone=client.whatsapp_phone or "",
                body=question,
            ),
            case_id=case.id,
            detected_intent=Intent.DOCUMENT_QUESTION,
            now=now - dt.timedelta(hours=4),
        )
        await tasks.create(
            organization.id,
            client_id=client.id,
            case_id=case.id,
            title=f"{client.display_name} asked a question",
            description=question,
            created_by="agent",
        )

        await session.commit()

    print(
        f"Seeded {FIRM}: {len(CLIENTS)} clients, one GST case each for {PERIOD}.\n"
        f"Sign in as {EMAIL} / {PASSWORD}\n"
        "No external credentials were used and no message was sent anywhere."
    )


async def main() -> int:
    try:
        await seed(reset="--reset" in sys.argv[1:])
    except RefusedInProduction as refusal:
        print(f"Refusing: {refusal}", file=sys.stderr)
        return 1
    finally:
        await dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
