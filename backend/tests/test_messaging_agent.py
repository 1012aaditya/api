"""Chasing clients: sending, refusing to send, and reading the replies."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest

from app.core.security import sign_payload
from app.db.session import get_session_factory
from app.models import (
    AgentPolicy,
    Client,
    ComplianceCase,
    ContactState,
    Direction,
    DocumentType,
    ExceptionType,
    Message,
    RequirementStatus,
    TaskStatus,
)
from app.providers.messaging.mock import MockWhatsAppProvider
from app.providers.messaging.registry import set_provider
from app.repositories.clients import RequirementRepository
from app.repositories.operations import (
    AgentEventRepository,
    AgentPolicyRepository,
    ExceptionRepository,
    TaskRepository,
)
from app.services.agent import AgentContext, ClientCommunicationAgent, describe_missing
from app.services.intent import Intent, read_intent
from app.services.messaging import MessagingService, in_quiet_hours
from tests.conftest import Tenant
from tests.fixtures.invoices import build_invoice_pdf
from tests.test_ingestion import make_case, make_client

PHONE = "+919876543210"
NOON = dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def whatsapp() -> MockWhatsAppProvider:
    provider = MockWhatsAppProvider()
    set_provider(provider)
    yield provider
    set_provider(None)


async def policy_for(tenant: Tenant, **changes) -> AgentPolicy:
    async with get_session_factory()() as session:
        policy = await AgentPolicyRepository(session).update(tenant.organization_id, changes)
        await session.commit()
        return policy


# --- who may be messaged -----------------------------------------------


async def test_an_opted_out_client_is_never_messaged(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    client = await make_client(tenant, whatsapp_phone=PHONE, allow_automated_contact=False)

    async with get_session_factory()() as session:
        sent = await MessagingService(session).send(
            await session.get(Client, client.id), "hello", now=NOON
        )
        await session.commit()

    assert sent is None
    assert whatsapp.sent == []


async def test_a_refusal_is_recorded_so_the_firm_can_see_it(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    client = await make_client(tenant, whatsapp_phone=PHONE, allow_automated_contact=False)

    async with get_session_factory()() as session:
        await MessagingService(session).send(
            await session.get(Client, client.id), "hello", now=NOON
        )
        await session.commit()

    async with get_session_factory()() as session:
        events = await AgentEventRepository(session).timeline(tenant.organization_id)
    withheld = [e for e in events if e.action == "whatsapp.withheld"]
    assert withheld, "a message the agent decided not to send must still be visible"
    assert withheld[0].details["code"] == "opted_out"


async def test_a_human_may_message_a_client_the_agent_may_not(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    """The caps restrain the agent, not the CA."""
    await policy_for(tenant, enabled=False)
    client = await make_client(tenant, whatsapp_phone=PHONE)

    async with get_session_factory()() as session:
        row = await session.get(Client, client.id)
        assert (await MessagingService(session).may_send(row, now=NOON)).allowed is False
        assert (
            await MessagingService(session).may_send(row, now=NOON, automated=False)
        ).allowed is True


async def test_the_daily_cap_stops_the_agent(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    await policy_for(tenant, max_messages_per_day=2)
    client = await make_client(tenant, whatsapp_phone=PHONE)

    async with get_session_factory()() as session:
        row = await session.get(Client, client.id)
        service = MessagingService(session)
        for index in range(3):
            await service.send(row, f"message {index}", now=NOON)
        await session.commit()

    assert len(whatsapp.sent) == 2


def test_quiet_hours_wrap_around_midnight() -> None:
    policy = AgentPolicy(organization_id="org", quiet_hours_start=21, quiet_hours_end=9)
    assert in_quiet_hours(policy, dt.datetime(2026, 9, 21, 22, tzinfo=dt.UTC))
    assert in_quiet_hours(policy, dt.datetime(2026, 9, 21, 3, tzinfo=dt.UTC))
    assert not in_quiet_hours(policy, dt.datetime(2026, 9, 21, 12, tzinfo=dt.UTC))


async def test_nothing_is_sent_during_quiet_hours(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    client = await make_client(tenant, whatsapp_phone=PHONE)
    async with get_session_factory()() as session:
        await MessagingService(session).send(
            await session.get(Client, client.id),
            "late",
            now=dt.datetime(2026, 9, 21, 23, tzinfo=dt.UTC),
        )
        await session.commit()
    assert whatsapp.sent == []


async def test_a_client_with_no_number_is_not_messaged(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    client = await make_client(tenant, whatsapp_phone=None)
    async with get_session_factory()() as session:
        assert (
            await MessagingService(session).send(
                await session.get(Client, client.id), "hello", now=NOON
            )
            is None
        )


# --- asking for what is missing ----------------------------------------


def test_the_message_names_documents_the_way_a_person_would() -> None:
    from app.models import DocumentRequirement

    requirements = [
        DocumentRequirement(organization_id="o", case_id="c", document_type=t)
        for t in (DocumentType.BANK_STATEMENT, DocumentType.CREDIT_NOTE)
    ]
    assert describe_missing(requirements) == "bank statement and credit notes"


async def test_the_agent_asks_for_exactly_what_is_outstanding(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    client = await make_client(tenant, whatsapp_phone=PHONE)
    case = await make_case(tenant, client.id)

    async with get_session_factory()() as session:
        # Two of the four required documents are already in.
        for requirement in await RequirementRepository(session).for_case(
            tenant.organization_id, case.id
        ):
            if requirement.document_type in (
                DocumentType.SALES_INVOICE,
                DocumentType.PURCHASE_INVOICE,
            ):
                requirement.move_to(RequirementStatus.RECEIVED)
                requirement.move_to(RequirementStatus.PROCESSING)
                requirement.move_to(RequirementStatus.VALID)
        await session.commit()

    async with get_session_factory()() as session:
        agent = ClientCommunicationAgent(
            session, AgentContext(organization_id=tenant.organization_id)
        )
        result = await agent.request_missing_documents(
            await session.get(ComplianceCase, case.id),
            firm_name="Sharma & Associates",
            now=NOON,
        )
        await session.commit()

    assert result.messages_sent == 1
    body = whatsapp.sent[0].body
    assert "bank statement" in body
    assert "gstr-2b" in body.lower()
    assert "sales invoices" not in body.lower(), "already received; must not be asked for"
    assert "Sharma & Associates" in body


async def test_requirements_are_marked_requested_only_after_a_send(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    """Marking first would tell the CA we asked when we had not."""
    client = await make_client(tenant, whatsapp_phone=PHONE, allow_automated_contact=False)
    case = await make_case(tenant, client.id)

    async with get_session_factory()() as session:
        agent = ClientCommunicationAgent(
            session, AgentContext(organization_id=tenant.organization_id)
        )
        result = await agent.request_missing_documents(
            await session.get(ComplianceCase, case.id), firm_name="Firm", now=NOON
        )
        await session.commit()

    assert result.messages_sent == 0
    async with get_session_factory()() as session:
        requirements = await RequirementRepository(session).for_case(
            tenant.organization_id, case.id
        )
    assert all(r.status == RequirementStatus.MISSING for r in requirements)


async def test_the_agent_cannot_reach_another_firms_client(
    tenant: Tenant, other_tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    """The organization comes from the context, never from an argument."""
    theirs = await make_client(other_tenant, whatsapp_phone=PHONE)

    async with get_session_factory()() as session:
        agent = ClientCommunicationAgent(
            session, AgentContext(organization_id=tenant.organization_id)
        )
        assert await agent.get_client(theirs.id) is None


# --- reading replies ----------------------------------------------------


async def test_a_promise_is_remembered_without_being_believed(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    client = await make_client(tenant, whatsapp_phone=PHONE)
    case = await make_case(tenant, client.id)

    async with get_session_factory()() as session:
        agent = ClientCommunicationAgent(
            session, AgentContext(organization_id=tenant.organization_id)
        )
        reading = read_intent("haan kal bhej dunga", today=NOON.date())
        result = await agent.handle_reply(
            await session.get(Client, client.id),
            reading,
            case=await session.get(ComplianceCase, case.id),
            text="haan kal bhej dunga",
            now=NOON,
        )
        await session.commit()

    assert reading.intent == Intent.DOCUMENT_COMMITMENT
    assert result.actions

    async with get_session_factory()() as session:
        from app.repositories.clients import ClientFactRepository

        fact = await ClientFactRepository(session).get(
            tenant.organization_id, client.id, "document_commitment_date"
        )
        requirements = await RequirementRepository(session).for_case(
            tenant.organization_id, case.id
        )
        row = await session.get(Client, client.id)

    assert fact is not None
    assert fact.value["date"] == "2026-09-22"
    assert row.contact_state == ContactState.COMMITTED
    # The promise changed nothing about what has actually arrived.
    assert all(r.status == RequirementStatus.MISSING for r in requirements)


async def test_i_already_sent_it_does_not_mark_anything_received(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    client = await make_client(tenant, whatsapp_phone=PHONE)
    case = await make_case(tenant, client.id)

    async with get_session_factory()() as session:
        agent = ClientCommunicationAgent(
            session, AgentContext(organization_id=tenant.organization_id)
        )
        await agent.handle_reply(
            await session.get(Client, client.id),
            read_intent("bhej diya sir"),
            case=await session.get(ComplianceCase, case.id),
            text="bhej diya sir",
            now=NOON,
        )
        await session.commit()

    async with get_session_factory()() as session:
        requirements = await RequirementRepository(session).for_case(
            tenant.organization_id, case.id
        )
        events = await AgentEventRepository(session).timeline(tenant.organization_id)

    assert all(r.status == RequirementStatus.MISSING for r in requirements)
    assert any(e.action == "client.claims_sent" for e in events)


async def test_do_not_contact_stops_everything(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    client = await make_client(tenant, whatsapp_phone=PHONE)

    async with get_session_factory()() as session:
        agent = ClientCommunicationAgent(
            session, AgentContext(organization_id=tenant.organization_id)
        )
        await agent.handle_reply(
            await session.get(Client, client.id),
            read_intent("ab message mat bhejo"),
            text="ab message mat bhejo",
            now=NOON,
        )
        await session.commit()

    async with get_session_factory()() as session:
        row = await session.get(Client, client.id)
        exceptions = await ExceptionRepository(session).list_for_organization(
            tenant.organization_id
        )
        # And nothing can be sent afterwards.
        assert await MessagingService(session).send(row, "anything", now=NOON) is None

    assert row.allow_automated_contact is False
    assert row.contact_state == ContactState.OPTED_OUT
    assert exceptions[0].type == ExceptionType.CLIENT_OPTED_OUT


async def test_asking_for_a_human_creates_a_task(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    client = await make_client(tenant, whatsapp_phone=PHONE)

    async with get_session_factory()() as session:
        agent = ClientCommunicationAgent(
            session, AgentContext(organization_id=tenant.organization_id)
        )
        await agent.handle_reply(
            await session.get(Client, client.id),
            read_intent("sir se baat karao"),
            text="sir se baat karao",
            now=NOON,
        )
        await session.commit()

    async with get_session_factory()() as session:
        tasks = await TaskRepository(session).list_for_organization(
            tenant.organization_id, status=TaskStatus.OPEN
        )
    assert tasks and "asked to speak to someone" in tasks[0].title


async def test_a_confused_client_is_told_what_is_needed(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    client = await make_client(tenant, whatsapp_phone=PHONE)
    case = await make_case(tenant, client.id)

    async with get_session_factory()() as session:
        agent = ClientCommunicationAgent(
            session, AgentContext(organization_id=tenant.organization_id)
        )
        result = await agent.handle_reply(
            await session.get(Client, client.id),
            read_intent("samajh nahi aa raha kya bhejna hai"),
            case=await session.get(ComplianceCase, case.id),
            text="samajh nahi aa raha kya bhejna hai",
            now=NOON,
        )
        await session.commit()

    assert result.messages_sent == 1
    assert "bank statement" in whatsapp.sent[-1].body


# --- the webhook --------------------------------------------------------


def webhook_url(tenant: Tenant) -> str:
    return f"/v1/inbound/whatsapp/{tenant.organization_id}"


async def test_an_inbound_text_is_processed_once(
    client: httpx.AsyncClient, tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    await make_client(tenant, whatsapp_phone=PHONE)
    payload = MockWhatsAppProvider.webhook_for_text(
        message_id="wamid.1", from_phone="919876543210", body="kal bhej dunga"
    )

    first = await client.post(webhook_url(tenant), json=payload)
    second = await client.post(webhook_url(tenant), json=payload)

    assert first.status_code == 200
    assert first.json()["handled"] == 1
    assert second.json()["handled"] == 0
    assert second.json()["skipped"] == ["already_processed"]

    async with get_session_factory()() as session:
        from sqlalchemy import select

        rows = (
            (await session.execute(select(Message).where(Message.direction == Direction.INBOUND)))
            .scalars()
            .all()
        )
    assert len(rows) == 1, "a redelivered webhook must not store the message twice"


async def test_a_message_from_an_unknown_number_is_filed_not_answered(
    client: httpx.AsyncClient, tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    payload = MockWhatsAppProvider.webhook_for_text(
        message_id="wamid.2", from_phone="911111111111", body="hello?"
    )
    response = await client.post(webhook_url(tenant), json=payload)

    assert response.json()["skipped"] == ["unknown_sender"]
    assert whatsapp.sent == [], "we do not reply to strangers"

    async with get_session_factory()() as session:
        items = await ExceptionRepository(session).list_for_organization(tenant.organization_id)
    assert items and items[0].type == "unknown_sender"


async def test_a_status_callback_is_accepted_and_ignored(
    client: httpx.AsyncClient, tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    response = await client.post(
        webhook_url(tenant),
        json={"entry": [{"changes": [{"value": {"statuses": [{"id": "x"}]}}]}]},
    )
    assert response.status_code == 200
    assert response.json()["handled"] == 0


async def test_an_unsigned_webhook_is_refused_when_a_secret_is_set(
    client: httpx.AsyncClient, tenant: Tenant, app, whatsapp: MockWhatsAppProvider
) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    original = settings.whatsapp_webhook_secret
    object.__setattr__(settings, "whatsapp_webhook_secret", "shhh")
    try:
        payload = MockWhatsAppProvider.webhook_for_text(
            message_id="wamid.3", from_phone="919876543210", body="hi"
        )
        unsigned = await client.post(webhook_url(tenant), json=payload)
        assert unsigned.status_code == 401

        import json as _json

        raw = _json.dumps(payload).encode()
        signed = await client.post(
            webhook_url(tenant),
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": f"sha256={sign_payload(raw, 'shhh')}",
            },
        )
        assert signed.status_code == 200
    finally:
        object.__setattr__(settings, "whatsapp_webhook_secret", original)


async def test_an_unknown_organization_is_not_confirmed(
    client: httpx.AsyncClient, whatsapp: MockWhatsAppProvider
) -> None:
    response = await client.post(
        "/v1/inbound/whatsapp/org_does_not_exist",
        json=MockWhatsAppProvider.webhook_for_text(
            message_id="wamid.4", from_phone="919876543210", body="hi"
        ),
    )
    assert response.status_code == 401


# --- a document arriving over WhatsApp ----------------------------------


async def test_a_document_sent_on_whatsapp_settles_its_requirement(
    client: httpx.AsyncClient, tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    """The §45 flow, end to end, with no external credentials anywhere."""
    person = await make_client(tenant, whatsapp_phone=PHONE)
    case = await make_case(tenant, person.id)

    whatsapp.register_media("media-1", build_invoice_pdf())
    payload = MockWhatsAppProvider.webhook_for_document(
        message_id="wamid.doc",
        from_phone="919876543210",
        media_reference="media-1",
        filename="september-invoice.pdf",
    )
    response = await client.post(webhook_url(tenant), json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["handled"] == 1

    async with get_session_factory()() as session:
        requirements = await RequirementRepository(session).for_case(
            tenant.organization_id, case.id
        )
        events = await AgentEventRepository(session).timeline(
            tenant.organization_id, client_id=person.id
        )

    settled = [r for r in requirements if r.status == RequirementStatus.VALID]
    assert settled, "an invoice that arrived should settle an invoice requirement"
    actions = {event.action for event in events}
    assert "whatsapp.received" in actions
    assert "document.classified" in actions


async def test_a_file_we_cannot_read_is_reported_to_the_firm(
    client: httpx.AsyncClient, tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    await make_client(tenant, whatsapp_phone=PHONE)
    whatsapp.register_media("media-bad", b"GIF89a" + b"\x00" * 40)
    payload = MockWhatsAppProvider.webhook_for_document(
        message_id="wamid.bad",
        from_phone="919876543210",
        media_reference="media-bad",
        filename="photo.gif",
    )
    response = await client.post(webhook_url(tenant), json=payload)
    assert response.status_code == 200

    async with get_session_factory()() as session:
        items = await ExceptionRepository(session).list_for_organization(tenant.organization_id)
    assert items and "cannot read" in items[0].message


# --- what a production deployment may not do ----------------------------


def production_settings(**overrides):
    from app.core.config import Settings, get_settings

    current = get_settings()
    return Settings(
        **{
            **{
                "app_env": "production",
                "database_url": current.database_url,
                "jwt_secret": current.jwt_secret,
            },
            **overrides,
        }
    )


def test_the_mock_provider_is_refused_in_production() -> None:
    """A mock that reports every message as sent is a lie in production.

    The dashboard would show "WhatsApp sent to Marigold Retail" for a message
    no phone ever received, and the firm would stop chasing (§28, §42).
    """
    from app.providers.messaging.registry import (
        MessagingUnavailableError,
        build_provider,
    )

    settings = production_settings(whatsapp_provider="mock")
    with pytest.raises(MessagingUnavailableError) as raised:
        build_provider(settings)
    assert "mock" in str(raised.value).lower()

    # And outside production it is exactly what you want.
    assert build_provider(production_settings(app_env="development")).name == "mock"


def test_the_mock_voice_provider_is_refused_in_production() -> None:
    from app.providers.messaging.voice import VoiceUnavailableError, build_provider

    with pytest.raises(VoiceUnavailableError):
        build_provider(production_settings(voice_provider="mock"))
