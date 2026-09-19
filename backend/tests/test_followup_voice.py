"""The follow-up ladder, and the voice rung at the top of it."""

from __future__ import annotations

import datetime as dt

import pytest

from app.db.session import get_session_factory
from app.models import (
    AgentJob,
    AgentJobStatus,
    AgentJobType,
    CallIntent,
    CallStatus,
    CaseStatus,
    Client,
    ComplianceCase,
    ExceptionType,
    RequirementStatus,
    TaskStatus,
)
from app.providers.messaging.mock import MockWhatsAppProvider
from app.providers.messaging.registry import set_provider
from app.providers.messaging.voice import MockVoiceProvider
from app.providers.messaging.voice import set_provider as set_voice_provider
from app.repositories.agent_jobs import AgentJobRepository
from app.repositories.clients import RequirementRepository
from app.repositories.operations import (
    AgentPolicyRepository,
    ExceptionRepository,
    TaskRepository,
)
from app.services.followup import (
    FollowUpEngine,
    drain_agent_jobs,
    sweep_cases_needing_chasing,
)
from app.services.voice import VoiceEngine, compose_script
from tests.conftest import Tenant
from tests.test_ingestion import make_case, make_client

PHONE = "+919876543210"
DAY0 = dt.datetime(2026, 9, 21, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def whatsapp() -> MockWhatsAppProvider:
    provider = MockWhatsAppProvider()
    set_provider(provider)
    yield provider
    set_provider(None)


@pytest.fixture
def voice() -> MockVoiceProvider:
    provider = MockVoiceProvider()
    set_voice_provider(provider)
    yield provider
    set_voice_provider(None)


async def policy_for(tenant: Tenant, **changes):
    async with get_session_factory()() as session:
        policy = await AgentPolicyRepository(session).update(tenant.organization_id, changes)
        await session.commit()
        return policy


async def setup_blocked_case(tenant: Tenant):
    client = await make_client(tenant, whatsapp_phone=PHONE, phone=PHONE)
    case = await make_case(tenant, client.id)
    return client, case


# --- scheduling ---------------------------------------------------------


async def test_the_first_rung_is_scheduled_for_later_not_now(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    _client, case = await setup_blocked_case(tenant)
    await policy_for(tenant, first_reminder_hours=24)

    async with get_session_factory()() as session:
        job = await FollowUpEngine(session).start_chasing(
            await session.get(ComplianceCase, case.id), now=DAY0
        )
        await session.commit()

    assert job is not None
    assert job.type == AgentJobType.SEND_FOLLOWUP
    assert job.available_at == DAY0 + dt.timedelta(hours=24)
    assert whatsapp.sent == [], "scheduling must not send anything yet"


async def test_scheduling_twice_queues_one_job(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    """A sweep that runs every few minutes must not stack reminders."""
    _client, case = await setup_blocked_case(tenant)

    async with get_session_factory()() as session:
        engine = FollowUpEngine(session)
        row = await session.get(ComplianceCase, case.id)
        await engine.start_chasing(row, now=DAY0)
        await engine.start_chasing(row, now=DAY0)
        await engine.start_chasing(row, now=DAY0)
        await session.commit()

    async with get_session_factory()() as session:
        pending = await AgentJobRepository(session).pending_for_case(
            tenant.organization_id, case.id
        )
    assert len(pending) == 1


async def test_the_sweep_finds_blocked_cases(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    for _ in range(3):
        await setup_blocked_case(tenant)

    async with get_session_factory()() as session:
        result = await sweep_cases_needing_chasing(session, tenant.organization_id, now=DAY0)
        await session.commit()

    assert result.scheduled == 3


# --- running a rung -----------------------------------------------------


async def test_a_due_reminder_sends_and_schedules_the_next(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    _client, case = await setup_blocked_case(tenant)
    await policy_for(tenant, first_reminder_hours=24, second_reminder_hours=48)

    async with get_session_factory()() as session:
        await FollowUpEngine(session).start_chasing(
            await session.get(ComplianceCase, case.id), now=DAY0
        )
        await session.commit()

    due = DAY0 + dt.timedelta(hours=25)
    async with get_session_factory()() as session:
        from app.core.config import get_settings

        ran = await drain_agent_jobs(session, settings=get_settings(), now=due)

    assert ran == 1
    assert len(whatsapp.sent) == 1
    assert "bank statement" in whatsapp.sent[0].body

    async with get_session_factory()() as session:
        pending = await AgentJobRepository(session).pending_for_case(
            tenant.organization_id, case.id
        )
    assert len(pending) == 1, "the next rung should be queued"
    assert pending[0].available_at == due + dt.timedelta(hours=48)


async def test_a_reminder_is_dropped_when_the_documents_arrived(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    """A schedule is an intention, re-checked at send time."""
    _client, case = await setup_blocked_case(tenant)

    async with get_session_factory()() as session:
        await FollowUpEngine(session).start_chasing(
            await session.get(ComplianceCase, case.id), now=DAY0
        )
        # Everything arrives before the reminder is due.
        for requirement in await RequirementRepository(session).for_case(
            tenant.organization_id, case.id
        ):
            if requirement.required:
                requirement.move_to(RequirementStatus.RECEIVED)
                requirement.move_to(RequirementStatus.PROCESSING)
                requirement.move_to(RequirementStatus.VALID)
        await session.commit()

    async with get_session_factory()() as session:
        from app.core.config import get_settings

        await drain_agent_jobs(session, settings=get_settings(), now=DAY0 + dt.timedelta(hours=25))

    assert whatsapp.sent == [], "nothing was outstanding; nothing should be sent"


async def test_an_opted_out_client_cancels_the_whole_ladder(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    client, case = await setup_blocked_case(tenant)

    async with get_session_factory()() as session:
        await FollowUpEngine(session).start_chasing(
            await session.get(ComplianceCase, case.id), now=DAY0
        )
        row = await session.get(Client, client.id)
        row.allow_automated_contact = False
        await session.commit()

    async with get_session_factory()() as session:
        from app.core.config import get_settings

        await drain_agent_jobs(session, settings=get_settings(), now=DAY0 + dt.timedelta(hours=25))

    assert whatsapp.sent == []
    async with get_session_factory()() as session:
        pending = await AgentJobRepository(session).pending_for_case(
            tenant.organization_id, case.id
        )
    assert pending == [], "queued work for an opted-out client must be cancelled"


# --- the ladder stops ---------------------------------------------------


async def test_the_ladder_ends_with_a_person_not_a_fourth_message(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    _client, case = await setup_blocked_case(tenant)
    await policy_for(tenant, max_followups_per_case=2, allow_voice_calls=False)

    async with get_session_factory()() as session:
        engine = FollowUpEngine(session)
        row = await session.get(ComplianceCase, case.id)
        # Past the ceiling.
        await engine.schedule_next(row, attempt_number=3, now=DAY0)
        await session.commit()

    async with get_session_factory()() as session:
        exceptions = await ExceptionRepository(session).list_for_organization(
            tenant.organization_id
        )
        tasks = await TaskRepository(session).list_for_organization(
            tenant.organization_id, status=TaskStatus.OPEN
        )
        row = await session.get(ComplianceCase, case.id)

    assert any(e.type == ExceptionType.MAX_FOLLOWUPS_REACHED for e in exceptions)
    assert any("Call" in task.title for task in tasks)
    assert row.status == CaseStatus.ESCALATED
    assert whatsapp.sent == []


async def test_voice_off_ends_the_ladder_rather_than_skipping_a_rung(
    tenant: Tenant, whatsapp: MockWhatsAppProvider, voice: MockVoiceProvider
) -> None:
    _client, case = await setup_blocked_case(tenant)
    await policy_for(tenant, max_followups_per_case=5, allow_voice_calls=False)

    async with get_session_factory()() as session:
        await FollowUpEngine(session).schedule_next(
            await session.get(ComplianceCase, case.id), attempt_number=3, now=DAY0
        )
        await session.commit()

    assert voice.calls == []
    async with get_session_factory()() as session:
        tasks = await TaskRepository(session).list_for_organization(
            tenant.organization_id, status=TaskStatus.OPEN
        )
    assert tasks, "if the agent cannot call, a person is asked to"


# --- voice --------------------------------------------------------------


async def test_the_call_identifies_itself_as_an_assistant(tenant: Tenant) -> None:
    client, case = await setup_blocked_case(tenant)
    async with get_session_factory()() as session:
        missing = await RequirementRepository(session).outstanding_for_case(
            tenant.organization_id, case.id
        )
        script = compose_script(
            firm_name="Sharma & Associates",
            case=await session.get(ComplianceCase, case.id),
            missing=missing,
            client=await session.get(Client, client.id),
        )
    assert "AI assistant" in script
    assert "Sharma & Associates" in script
    assert "bank statement" in script


async def test_no_call_is_placed_when_the_firm_has_not_enabled_it(
    tenant: Tenant, voice: MockVoiceProvider
) -> None:
    """Voice is off by default; it must stay off until switched on."""
    client, case = await setup_blocked_case(tenant)

    async with get_session_factory()() as session:
        call = await VoiceEngine(session).place_call(
            case=await session.get(ComplianceCase, case.id),
            client=await session.get(Client, client.id),
            now=DAY0,
        )
        await session.commit()

    assert call is None
    assert voice.calls == []


async def test_a_call_is_placed_once_enabled(tenant: Tenant, voice: MockVoiceProvider) -> None:
    await policy_for(tenant, allow_voice_calls=True)
    client, case = await setup_blocked_case(tenant)

    async with get_session_factory()() as session:
        call = await VoiceEngine(session).place_call(
            case=await session.get(ComplianceCase, case.id),
            client=await session.get(Client, client.id),
            now=DAY0,
        )
        await session.commit()

    assert call is not None
    assert call.status == CallStatus.DIALING
    assert len(voice.calls) == 1


async def test_the_daily_call_cap_holds(tenant: Tenant, voice: MockVoiceProvider) -> None:
    await policy_for(tenant, allow_voice_calls=True, max_calls_per_day=1)
    client, case = await setup_blocked_case(tenant)

    async with get_session_factory()() as session:
        engine = VoiceEngine(session)
        case_row = await session.get(ComplianceCase, case.id)
        client_row = await session.get(Client, client.id)
        await engine.place_call(case=case_row, client=client_row, now=DAY0)
        await engine.place_call(case=case_row, client=client_row, now=DAY0)
        await session.commit()

    assert len(voice.calls) == 1


async def test_a_commitment_on_a_call_is_recorded_not_believed(
    tenant: Tenant, whatsapp: MockWhatsAppProvider, voice: MockVoiceProvider
) -> None:
    await policy_for(tenant, allow_voice_calls=True)
    client, case = await setup_blocked_case(tenant)

    async with get_session_factory()() as session:
        engine = VoiceEngine(session)
        call = await engine.place_call(
            case=await session.get(ComplianceCase, case.id),
            client=await session.get(Client, client.id),
            now=DAY0,
        )
        await engine.record_outcome(
            call,
            status=CallStatus.COMPLETED,
            transcript="haan kal bhej dunga",
            duration_seconds=31,
            now=DAY0,
        )
        await session.commit()

    async with get_session_factory()() as session:
        from app.repositories.clients import ClientFactRepository

        fact = await ClientFactRepository(session).get(
            tenant.organization_id, client.id, "document_commitment_date"
        )
        requirements = await RequirementRepository(session).for_case(
            tenant.organization_id, case.id
        )

    assert fact is not None and fact.value["date"] == "2026-09-22"
    assert all(r.status == RequirementStatus.MISSING for r in requirements), (
        "a promise on a call settles nothing"
    )


async def test_do_not_contact_on_a_call_cancels_queued_work(
    tenant: Tenant, whatsapp: MockWhatsAppProvider, voice: MockVoiceProvider
) -> None:
    await policy_for(tenant, allow_voice_calls=True)
    client, case = await setup_blocked_case(tenant)

    async with get_session_factory()() as session:
        await FollowUpEngine(session).start_chasing(
            await session.get(ComplianceCase, case.id), now=DAY0
        )
        engine = VoiceEngine(session)
        call = await engine.place_call(
            case=await session.get(ComplianceCase, case.id),
            client=await session.get(Client, client.id),
            now=DAY0,
        )
        await engine.record_outcome(
            call,
            status=CallStatus.COMPLETED,
            intent=CallIntent.DO_NOT_CONTACT,
            transcript="please stop calling me",
            now=DAY0,
        )
        await session.commit()

    async with get_session_factory()() as session:
        row = await session.get(Client, client.id)
        pending = await AgentJobRepository(session).pending_for_case(
            tenant.organization_id, case.id
        )
    assert row.allow_automated_contact is False
    assert pending == []


async def test_an_unanswered_call_changes_nothing(tenant: Tenant, voice: MockVoiceProvider) -> None:
    await policy_for(tenant, allow_voice_calls=True)
    client, case = await setup_blocked_case(tenant)

    async with get_session_factory()() as session:
        engine = VoiceEngine(session)
        call = await engine.place_call(
            case=await session.get(ComplianceCase, case.id),
            client=await session.get(Client, client.id),
            now=DAY0,
        )
        await engine.record_outcome(call, status=CallStatus.NO_ANSWER, now=DAY0)
        await session.commit()

    async with get_session_factory()() as session:
        row = await session.get(Client, client.id)
    assert row.allow_automated_contact is True


# --- the worker ---------------------------------------------------------


async def test_the_worker_drains_both_queues(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    _client, case = await setup_blocked_case(tenant)
    async with get_session_factory()() as session:
        await FollowUpEngine(session).start_chasing(
            await session.get(ComplianceCase, case.id), now=DAY0
        )
        await session.commit()

    async with get_session_factory()() as session:
        job = (await AgentJobRepository(session).pending_for_case(tenant.organization_id, case.id))[
            0
        ]
        job.available_at = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
        await session.commit()

    from app.workers.worker import run_once

    summary = await run_once()
    assert "agent_jobs" in summary
    assert summary["agent_jobs"] == 1
    assert len(whatsapp.sent) == 1


async def test_a_claimed_job_is_not_claimed_again(tenant: Tenant) -> None:
    _client, case = await setup_blocked_case(tenant)
    async with get_session_factory()() as session:
        await FollowUpEngine(session).start_chasing(
            await session.get(ComplianceCase, case.id), now=DAY0
        )
        await session.commit()

    due = DAY0 + dt.timedelta(hours=48)
    async with get_session_factory()() as session:
        repo = AgentJobRepository(session)
        first = await repo.claim_next(now=due)
        await session.commit()
        second = await repo.claim_next(now=due)

    assert first is not None
    assert second is None
    assert first.status == AgentJobStatus.PROCESSING


async def test_a_job_a_dead_worker_left_is_requeued(tenant: Tenant) -> None:
    _client, case = await setup_blocked_case(tenant)
    async with get_session_factory()() as session:
        await FollowUpEngine(session).start_chasing(
            await session.get(ComplianceCase, case.id), now=DAY0
        )
        await session.commit()

    due = DAY0 + dt.timedelta(hours=48)
    async with get_session_factory()() as session:
        repo = AgentJobRepository(session)
        job = await repo.claim_next(now=due)
        job.claimed_at = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
        await session.commit()

    async with get_session_factory()() as session:
        released = await AgentJobRepository(session).release_stale(
            older_than=dt.datetime(2021, 1, 1, tzinfo=dt.UTC)
        )
        await session.commit()

    assert released == 1
    async with get_session_factory()() as session:
        row = await session.get(AgentJob, job.id)
    assert row.status == AgentJobStatus.QUEUED
