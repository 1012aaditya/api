"""The one place a model is allowed to decide.

These tests are mostly about what happens when it decides badly. The planner
is the only component in this product that can put words in the firm's mouth,
so the interesting cases are the ones where its suggestion is thrown away and
the deterministic ladder runs instead — quietly, and on the record.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from app.db.session import get_session_factory
from app.models import (
    AgentJob,
    AgentJobStatus,
    CaseStatus,
    ComplianceCase,
    ExceptionType,
    RequirementStatus,
    TaskStatus,
)
from app.providers.base import ProviderResult, ProviderUsage
from app.providers.messaging.mock import MockWhatsAppProvider
from app.providers.messaging.registry import set_provider as set_whatsapp
from app.providers.registry import set_provider as set_ai
from app.repositories.agent_jobs import AgentJobRepository
from app.repositories.operations import (
    AgentEventRepository,
    AgentPolicyRepository,
    ExceptionRepository,
    TaskRepository,
)
from app.services.followup import FollowUpEngine, drain_agent_jobs
from app.services.planner import (
    CaseSnapshot,
    Plan,
    render_user_prompt,
    validate,
)
from tests.conftest import Tenant
from tests.test_ingestion import make_case, make_client

PHONE = "+919876543210"
DAY0 = dt.datetime(2026, 9, 21, 10, 0, tzinfo=dt.UTC)


class ScriptedModel:
    """A model that says whatever the test told it to say."""

    name = "scripted"
    model = "scripted-model-v1"

    def __init__(self, reply: dict | str) -> None:
        self.reply = reply
        self.prompts: list[list[dict]] = []

    async def complete(self, *, messages: list[dict], json_mode: bool) -> ProviderResult:
        self.prompts.append(messages)
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return ProviderResult(
            text=text,
            usage=ProviderUsage(input_tokens=800, output_tokens=60),
            model=self.model,
            latency_ms=120,
        )

    async def aclose(self) -> None:
        return None


@pytest.fixture
def whatsapp() -> MockWhatsAppProvider:
    provider = MockWhatsAppProvider()
    set_whatsapp(provider)
    yield provider
    set_whatsapp(None)


def thinking_settings():
    """Settings that claim a model is configured, for the registry's sake."""
    from app.core.config import get_settings

    return get_settings().model_copy(
        update={"ai_api_key": "test-key-not-real", "ai_model": "scripted-model-v1"}
    )


def snapshot(**overrides) -> CaseSnapshot:
    base = {
        "firm_name": "Sharma & Associates",
        "client_name": "Marigold Retail Pvt Ltd",
        "language": "hinglish",
        "contact_state": "contacted",
        "case_label": "GST — 2026-09",
        "period": "2026-09",
        "days_to_deadline": 12,
        "missing": ["Bank statement", "GSTR-2B"],
        "settled": ["Sales invoices"],
        "reminders_sent": 1,
        "hours_since_last_message": 30,
    }
    base.update(overrides)
    return CaseSnapshot(**base)


async def blocked_case(tenant: Tenant):
    client = await make_client(tenant, whatsapp_phone=PHONE, phone=PHONE)
    case = await make_case(tenant, client.id)
    return client, case


async def run_the_rung(tenant: Tenant, case_id: str, model: ScriptedModel, *, now=None):
    """Queue a follow-up, then run it with this model installed."""
    settings = thinking_settings()
    set_ai(model)
    try:
        async with get_session_factory()() as session:
            await FollowUpEngine(session, settings=settings).start_chasing(
                await session.get(ComplianceCase, case_id), now=DAY0
            )
            await session.commit()
        async with get_session_factory()() as session:
            ran = await drain_agent_jobs(
                session, settings=settings, now=now or DAY0 + dt.timedelta(hours=25)
            )
            await session.commit()
        return ran
    finally:
        set_ai(None)


async def events_for(tenant: Tenant) -> list:
    async with get_session_factory()() as session:
        return await AgentEventRepository(session).timeline(tenant.organization_id)


# --- what it may say ----------------------------------------------------


def test_a_usable_plan_passes() -> None:
    plan = Plan(
        action="send_message",
        message="Namaste! GST filing ke liye bank statement aur GSTR-2B chahiye. Bhej dijiye?",
    )
    assert validate(plan, snapshot()) is None


@pytest.mark.parametrize(
    "message,expected",
    [
        (
            "Thanks for sending the bank statement! GSTR-2B bhi chahiye.",
            "implies a document was received",
        ),
        (
            "Bank statement upload here: https://docs.example.com/upload",
            "contains a link",
        ),
        (
            "Please send your sales invoices for September.",
            "already has",
        ),
        (
            "Namaste, aap kaise hain? Sab theek?",
            "does not ask for anything that is actually missing",
        ),
        ("", "no message in it"),
    ],
)
def test_a_message_that_would_embarrass_the_firm_is_rejected(
    message: str, expected: str
) -> None:
    plan = Plan(action="send_message", message=message)
    reason = validate(plan, snapshot())
    assert reason is not None and expected in reason


def test_an_action_outside_the_vocabulary_is_rejected() -> None:
    plan = Plan(action="call_the_client_now")
    assert "is not an action this agent has" in (validate(plan, snapshot()) or "")


def test_a_wait_has_to_be_a_usable_length() -> None:
    assert validate(Plan(action="wait", wait_hours=0), snapshot()) is not None
    assert validate(Plan(action="wait", wait_hours=99999), snapshot()) is not None
    assert validate(Plan(action="wait", wait_hours=72), snapshot()) is None


def test_a_very_long_message_is_rejected() -> None:
    plan = Plan(action="send_message", message="bank statement " * 100)
    assert "characters" in (validate(plan, snapshot()) or "")


# --- what it is told ----------------------------------------------------


def test_the_prompt_carries_no_identifiers() -> None:
    """The model works on one case and never names another (§20)."""
    text = render_user_prompt(snapshot())
    for prefix in ("cli_", "case_", "org_", "req_", "usr_"):
        assert prefix not in text


def test_the_prompt_says_what_may_and_may_not_be_asked_for() -> None:
    text = render_user_prompt(snapshot())
    assert "Bank statement" in text
    assert "do NOT ask for these" in text
    assert "Sales invoices" in text
    assert "Hinglish" in text


# --- what happens with what it decides ----------------------------------


async def test_the_clients_own_words_go_out_not_the_template(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    _client, case = await blocked_case(tenant)
    model = ScriptedModel(
        {
            "reasoning": "They replied in Hinglish and have been asked once.",
            "action": "send_message",
            "message": (
                "Namaste sir, September GST ke liye bank statement aur "
                "GSTR-2B chahiye. Aaj bhej denge?"
            ),
        }
    )

    assert await run_the_rung(tenant, case.id, model) == 1

    assert len(whatsapp.sent) == 1
    assert "Namaste sir" in whatsapp.sent[0].body
    actions = {event.action for event in await events_for(tenant)}
    assert "agent.planned" in actions

    async with get_session_factory()() as session:
        from app.repositories.clients import RequirementRepository

        requirements = await RequirementRepository(session).for_case(
            tenant.organization_id, case.id
        )
    assert any(r.status == RequirementStatus.REQUESTED for r in requirements), (
        "the requirements are asked-for because a message went out, however it was written"
    )


async def test_a_rejected_plan_falls_back_to_the_composed_message(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    """The client is still chased. The firm still sees what was ignored."""
    _client, case = await blocked_case(tenant)
    model = ScriptedModel(
        {
            "reasoning": "Being friendly.",
            "action": "send_message",
            "message": "Thanks for sending your bank statement! Just GSTR-2B left now.",
        }
    )

    await run_the_rung(tenant, case.id, model)

    assert len(whatsapp.sent) == 1, "the chase is not skipped because the model slipped"
    assert "Thanks for sending" not in whatsapp.sent[0].body
    assert "bank statement" in whatsapp.sent[0].body.lower()

    rejected = [e for e in await events_for(tenant) if e.action == "agent.plan_rejected"]
    assert rejected, "a suggestion that was thrown away has to be visible"
    assert "received" in rejected[0].summary


async def test_a_decision_to_wait_sends_nothing_and_comes_back_later(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    _client, case = await blocked_case(tenant)
    model = ScriptedModel(
        {
            "reasoning": "They said their audit is on until the 20th.",
            "action": "wait",
            "wait_hours": 168,
            "reason": "client is mid-audit",
        }
    )

    due = DAY0 + dt.timedelta(hours=25)
    await run_the_rung(tenant, case.id, model, now=due)

    assert whatsapp.sent == [], "waiting means waiting"
    async with get_session_factory()() as session:
        pending = await AgentJobRepository(session).pending_for_case(
            tenant.organization_id, case.id
        )
    assert len(pending) == 1
    assert pending[0].available_at == due + dt.timedelta(hours=168)


async def test_a_decision_to_escalate_hands_the_case_to_a_person(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    client, case = await blocked_case(tenant)
    model = ScriptedModel(
        {
            "reasoning": "The client is angry and asked to speak to someone.",
            "action": "escalate",
            "reason": "client is upset",
        }
    )

    await run_the_rung(tenant, case.id, model)

    assert whatsapp.sent == []
    async with get_session_factory()() as session:
        exceptions = await ExceptionRepository(session).list_for_organization(
            tenant.organization_id
        )
        tasks = await TaskRepository(session).list_for_organization(
            tenant.organization_id
        )
        row = await session.get(ComplianceCase, case.id)
    assert any(e.type == ExceptionType.MAX_FOLLOWUPS_REACHED for e in exceptions)
    assert tasks and tasks[0].status == TaskStatus.OPEN
    assert row.status == CaseStatus.ESCALATED


async def test_a_decision_to_create_a_task_keeps_the_chase_going(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    _client, case = await blocked_case(tenant)
    model = ScriptedModel(
        {
            "reasoning": "They asked whether an old invoice counts.",
            "action": "create_task",
            "task_title": "Marigold asked whether a March invoice counts for September",
            "task_description": "Client's question needs a person.",
        }
    )

    await run_the_rung(tenant, case.id, model)

    async with get_session_factory()() as session:
        tasks = await TaskRepository(session).list_for_organization(
            tenant.organization_id
        )
        pending = await AgentJobRepository(session).pending_for_case(
            tenant.organization_id, case.id
        )
    assert tasks and "March invoice" in tasks[0].title
    assert pending, "a question for a person does not end the chase"


async def test_nonsense_from_the_model_does_not_stop_the_chase(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    _client, case = await blocked_case(tenant)
    await run_the_rung(tenant, case.id, ScriptedModel("I'm afraid I can't help with that."))

    assert len(whatsapp.sent) == 1, "the deterministic ladder still ran"
    rejected = [e for e in await events_for(tenant) if e.action == "agent.plan_rejected"]
    assert rejected and "JSON" in rejected[0].summary


async def test_the_model_cannot_send_past_the_policy(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    """A plan is a suggestion. The cap is not."""
    _client, case = await blocked_case(tenant)
    async with get_session_factory()() as session:
        await AgentPolicyRepository(session).update(
            tenant.organization_id, {"max_messages_per_day": 0}
        )
        await session.commit()

    model = ScriptedModel(
        {
            "reasoning": "Time to ask again.",
            "action": "send_message",
            "message": "Sir, bank statement aur GSTR-2B bhej dijiye please.",
        }
    )
    await run_the_rung(tenant, case.id, model)

    assert whatsapp.sent == [], "the daily limit is not the model's to overrule"
    async with get_session_factory()() as session:
        from sqlalchemy import select

        jobs = (
            (
                await session.execute(
                    select(AgentJob).where(AgentJob.case_id == case.id)
                )
            )
            .scalars()
            .all()
        )
    assert any(job.status == AgentJobStatus.QUEUED for job in jobs), (
        "and the rung is owed again tomorrow rather than spent"
    )


async def test_a_firm_that_keeps_documents_local_is_not_given_a_remote_model(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    """The switch on the Agent page has to actually switch something (§19)."""
    _client, case = await blocked_case(tenant)
    async with get_session_factory()() as session:
        await AgentPolicyRepository(session).update(
            tenant.organization_id, {"local_ai_only": True}
        )
        await session.commit()

    model = ScriptedModel({"action": "send_message", "message": "anything at all"})
    settings = thinking_settings().model_copy(
        update={"ai_base_url": "https://api.openai.com/v1"}
    )
    set_ai(model)
    try:
        async with get_session_factory()() as session:
            await FollowUpEngine(session, settings=settings).start_chasing(
                await session.get(ComplianceCase, case.id), now=DAY0
            )
            await session.commit()
        async with get_session_factory()() as session:
            await drain_agent_jobs(
                session, settings=settings, now=DAY0 + dt.timedelta(hours=25)
            )
            await session.commit()
    finally:
        set_ai(None)

    assert model.prompts == [], "no case went to a model outside the premises"
    assert len(whatsapp.sent) == 1, "the client is still chased, by the rules"
    actions = {event.action for event in await events_for(tenant)}
    assert "agent.planning_skipped" in actions


async def test_planning_can_be_switched_off_entirely(
    tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    _client, case = await blocked_case(tenant)
    async with get_session_factory()() as session:
        await AgentPolicyRepository(session).update(
            tenant.organization_id, {"allow_ai_planning": False}
        )
        await session.commit()

    model = ScriptedModel(
        {"action": "send_message", "message": "Bank statement bhej dijiye."}
    )
    await run_the_rung(tenant, case.id, model)

    assert model.prompts == []
    assert len(whatsapp.sent) == 1
    assert "we still need" in whatsapp.sent[0].body.lower()


# --- the same promise, on the extraction side ---------------------------


async def test_a_local_only_firm_does_not_have_its_documents_extracted_remotely(
    tenant: Tenant,
) -> None:
    """The switch covers documents, not only the agent's reasoning.

    A worker picking up a queued job is exactly the path a firm would never
    see, which is why it is worth a test of its own.
    """
    from app.models import Document, ExtractionJob, JobStatus
    from app.repositories.jobs import JobRepository
    from app.services.extraction_service import ExtractionService

    async with get_session_factory()() as session:
        await AgentPolicyRepository(session).update(
            tenant.organization_id, {"local_ai_only": True}
        )
        document = Document(
            organization_id=tenant.organization_id,
            filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=2048,
            page_count=1,
            checksum_sha256="a" * 64,
            storage_key="some/key.pdf",
            source="upload",
        )
        session.add(document)
        await session.flush()
        job = await JobRepository(session).create(
            organization_id=tenant.organization_id,
            document_id=document.id,
            request_id=None,
        )
        await session.commit()
        job_id = job.id

    settings = thinking_settings().model_copy(
        update={"ai_base_url": "https://api.openai.com/v1"}
    )
    async with get_session_factory()() as session:
        row = await session.get(ExtractionJob, job_id)
        completed = await ExtractionService(session).process_job(row, settings=settings)
        await session.commit()

    assert completed is False
    async with get_session_factory()() as session:
        row = await session.get(ExtractionJob, job_id)
    assert row.status == JobStatus.FAILED
    assert row.error_code == "model_not_local"
    assert "own hardware" in row.error_message or "not local" in row.error_message


# --- "show me what you would do" ----------------------------------------


async def test_a_dry_run_names_the_clients_and_queues_nothing(
    client, auth_headers, tenant: Tenant, whatsapp: MockWhatsAppProvider
) -> None:
    """The question a CA asks before turning this loose on their client list."""
    person, case = await blocked_case(tenant)

    response = await client.post(
        "/v1/agent/run", headers=auth_headers, json={"dry_run": True}
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]

    assert body["scheduled"] == 1
    assert body["sent"] == 0
    assert any(person.display_name in line for line in body["skipped"])
    assert any("bank statement" in line.lower() for line in body["skipped"])

    async with get_session_factory()() as session:
        pending = await AgentJobRepository(session).pending_for_case(
            tenant.organization_id, case.id
        )
    assert pending == [], "a dry run must leave the queue exactly as it found it"
    assert whatsapp.sent == []
