"""The whole journey, in the order a firm lives it (§45).

Everything below runs with no external credentials: the WhatsApp provider is
the mock, the object store is in memory, and no AI provider is configured.
The unit tests elsewhere prove each part in isolation; this file proves they
add up to the product — a CA adds a client, the system works out what is
missing, chases it, reads what comes back, refuses to decide what it cannot,
and hands the rest to a person.

It is also the only coverage the operations API has, so the CA's own actions
go through HTTP rather than through the services behind it.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest

from app.db.base import utcnow
from app.db.session import get_session_factory
from app.models import (
    CaseStatus,
    Client,
    ComplianceCase,
    ContactState,
    Document,
    DocumentType,
    ExceptionStatus,
    ExceptionType,
    RequirementStatus,
    Severity,
)
from app.providers.messaging.mock import MockWhatsAppProvider
from app.providers.messaging.registry import set_provider
from app.repositories.clients import (
    CaseRepository,
    ClientFactRepository,
    ClientRepository,
    RequirementRepository,
)
from app.services.followup import drain_agent_jobs
from app.services.ingestion import IngestionService, refresh_case_status
from tests.conftest import Tenant
from tests.fixtures.invoices import InvoiceSpec, build_invoice_pdf, build_statement_pdf

PHONE = "+919876543210"
FROM = "919876543210"
GSTIN = "29AABCU9603R1ZJ"
SOMEBODY_ELSE = "27AAACA1111A1ZS"
PERIOD = "2026-09"


@pytest.fixture
def whatsapp() -> MockWhatsAppProvider:
    provider = MockWhatsAppProvider()
    set_provider(provider)
    yield provider
    set_provider(None)


def data(response: httpx.Response) -> dict | list:
    assert response.status_code < 300, response.text
    return response.json()["data"]


def webhook_url(tenant: Tenant) -> str:
    return f"/v1/inbound/whatsapp/{tenant.organization_id}"


async def drain(settings, *, now: dt.datetime) -> int:
    """Run the agent's queue the way the worker does."""
    async with get_session_factory()() as session:
        ran = await drain_agent_jobs(session, settings=settings, limit=10, now=now)
        await session.commit()
    return ran


def in_working_hours(moment: dt.datetime) -> dt.datetime:
    """The next 11:00 UTC at or after ``moment``.

    The firm's quiet hours are 21:00-09:00, so a journey driven from the wall
    clock sends nothing when the suite happens to run at night. A firm chases
    its clients in the daytime; so does this test.
    """
    candidate = moment.replace(hour=11, minute=0, second=0, microsecond=0)
    return candidate if candidate >= moment else candidate + dt.timedelta(days=1)


async def statuses(tenant: Tenant, case_id: str) -> dict[str, str]:
    async with get_session_factory()() as session:
        rows = await RequirementRepository(session).for_case(tenant.organization_id, case_id)
    return {row.document_type: row.status for row in rows}


async def test_the_whole_journey(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    tenant: Tenant,
    whatsapp: MockWhatsAppProvider,
    settings,
) -> None:
    start = utcnow()

    # 1. The CA adds a client. -------------------------------------------
    person = data(
        await client.post(
            "/v1/clients",
            headers=auth_headers,
            json={
                "name": "Marigold Retail",
                "business_name": "Marigold Retail Pvt Ltd",
                "client_code": "C001",
                "whatsapp_phone": PHONE,
                "gstin": GSTIN,
                "preferred_language": "hinglish",
            },
        )
    )
    assert person["contact_state"] == ContactState.NOT_CONTACTED

    # 2. And opens this month's GST case. --------------------------------
    case = data(
        await client.post(
            "/v1/cases",
            headers=auth_headers,
            json={"client_id": person["id"], "type": "gst", "period": PERIOD},
        )
    )

    # 3. The system works out what the filing needs, and nothing has been
    #    asked for yet.
    assert case["status"] == CaseStatus.BLOCKED
    assert {r["document_type"] for r in case["requirements"]} >= {
        DocumentType.SALES_INVOICE,
        DocumentType.PURCHASE_INVOICE,
        DocumentType.BANK_STATEMENT,
        DocumentType.GSTR_2B,
    }
    assert all(r["status"] == RequirementStatus.MISSING for r in case["requirements"])

    missing = data(
        await client.get(f"/v1/clients/{person['id']}/missing-documents", headers=auth_headers)
    )
    assert {r["document_type"] for r in missing} == {
        DocumentType.SALES_INVOICE,
        DocumentType.PURCHASE_INVOICE,
        DocumentType.BANK_STATEMENT,
        DocumentType.GSTR_2B,
    }

    # 4. The command centre shows one client blocked. --------------------
    centre = data(await client.get("/v1/command-centre", headers=auth_headers))
    assert centre["clients_total"] == 1
    assert centre["cases_blocked"] == 1
    assert centre["cases_ready"] == 0

    # 5. The CA presses "chase what needs chasing". Nothing is sent yet:
    #    the sweep queues, the worker sends.
    run = data(await client.post("/v1/agent/run", headers=auth_headers, json={}))
    assert run["scheduled"] == 1
    assert run["sent"] == 0
    assert whatsapp.sent == []

    # 6. A day later the worker sends the first ask. ---------------------
    assert await drain(settings, now=in_working_hours(start + dt.timedelta(hours=25))) == 1
    assert len(whatsapp.sent) == 1
    first = whatsapp.sent[0]
    assert first.to == PHONE
    assert "bank statement" in first.body.lower()
    assert tenant.api_key not in first.body

    after_asking = await statuses(tenant, case["id"])
    assert after_asking[DocumentType.BANK_STATEMENT] == RequirementStatus.REQUESTED

    # 7. The client answers in Hinglish that it is coming tomorrow. ------
    reply = MockWhatsAppProvider.webhook_for_text(
        message_id="wamid.promise", from_phone=FROM, body="kal bhej dunga bhai"
    )
    assert (await client.post(webhook_url(tenant), json=reply)).json()["handled"] == 1

    # The promise is remembered, not believed: nothing is marked received.
    async with get_session_factory()() as session:
        fact = await ClientFactRepository(session).get(
            tenant.organization_id, person["id"], "document_commitment_date"
        )
        row = await session.get(Client, person["id"])
        assert row.contact_state == ContactState.COMMITTED
    assert fact is not None
    assert (await statuses(tenant, case["id"]))[
        DocumentType.BANK_STATEMENT
    ] == RequirementStatus.REQUESTED

    # 8. The provider redelivers the same message. Nothing happens twice.
    assert (await client.post(webhook_url(tenant), json=reply)).json()["handled"] == 0

    # 9. The bank statement arrives on WhatsApp. -------------------------
    whatsapp.register_media("media-statement", build_statement_pdf())
    document = MockWhatsAppProvider.webhook_for_document(
        message_id="wamid.statement",
        from_phone=FROM,
        media_reference="media-statement",
        filename="sept-statement.pdf",
    )
    assert (await client.post(webhook_url(tenant), json=document)).json()["handled"] == 1

    after_statement = await statuses(tenant, case["id"])
    assert after_statement[DocumentType.BANK_STATEMENT] == RequirementStatus.VALID

    # 10. The next two documents arrive through the extraction pipeline,
    #     which is where an invoice's fields come from.
    await ingest_invoice(
        tenant,
        case_id=case["id"],
        client_id=person["id"],
        filename="sept-purchases.pdf",
        buyer=GSTIN,
        supplier=SOMEBODY_ELSE,
    )
    await ingest_invoice(
        tenant,
        case_id=case["id"],
        client_id=person["id"],
        filename="sept-sales.pdf",
        buyer=SOMEBODY_ELSE,
        supplier=GSTIN,
    )
    after_invoices = await statuses(tenant, case["id"])
    assert after_invoices[DocumentType.PURCHASE_INVOICE] == RequirementStatus.VALID
    assert after_invoices[DocumentType.SALES_INVOICE] == RequirementStatus.VALID

    # 11. Then one that belongs to somebody else entirely. It is not filed
    #     under a guess — it stops and waits for a person (§G, §16).
    await ingest_invoice(
        tenant,
        case_id=case["id"],
        client_id=person["id"],
        filename="not-theirs.pdf",
        buyer=SOMEBODY_ELSE,
        supplier="27AAACB2222B1ZJ",
    )

    exceptions = data(await client.get("/v1/exceptions", headers=auth_headers))
    mismatch = [e for e in exceptions if e["type"] == ExceptionType.GSTIN_MISMATCH]
    assert mismatch, "an invoice naming neither party should not be accepted quietly"
    assert mismatch[0]["severity"] == Severity.HIGH
    assert mismatch[0]["client_name"] == "Marigold Retail Pvt Ltd"

    # 12. A blocking exception keeps the case off "ready", even though only
    #     the GSTR-2B is still outstanding.
    assert await recomputed(tenant, case["id"]) == CaseStatus.BLOCKED

    # 13. The person deals with it, in their own words, on the record.
    resolved = data(
        await client.post(
            f"/v1/exceptions/{mismatch[0]['id']}/resolve",
            headers=auth_headers,
            json={
                "status": ExceptionStatus.RESOLVED,
                "note": "Rang them: it was their sister concern's invoice.",
            },
        )
    )
    assert resolved["status"] == ExceptionStatus.RESOLVED
    assert resolved["resolved_at"] is not None

    # 14. The last document arrives, and the case is ready to file. ------
    async with get_session_factory()() as session:
        requirement = await RequirementRepository(session).match_document_type(
            tenant.organization_id, case["id"], DocumentType.GSTR_2B
        )
        requirement.move_to(RequirementStatus.RECEIVED)
        requirement.move_to(RequirementStatus.PROCESSING)
        requirement.move_to(RequirementStatus.VALID)
        await session.commit()

    assert await recomputed(tenant, case["id"]) == CaseStatus.READY

    # 15. Which is what the firm sees. -----------------------------------
    centre = data(await client.get("/v1/command-centre", headers=auth_headers))
    assert centre["cases_ready"] == 1
    assert centre["cases_blocked"] == 0
    assert centre["exceptions_open"] == 0

    # 16. And the whole thing is on the record: what was sent, what came
    #     back, what was read from it, and what the firm decided.
    activity = data(await client.get("/v1/agent/activity?limit=100", headers=auth_headers))
    actions = [event["action"] for event in activity]
    for expected in (
        "case.created",
        "whatsapp.sent",
        "whatsapp.received",
        "document.classified",
        "requirement.updated",
        "exception.raised",
        "exception.resolved",
    ):
        assert expected in actions, f"{expected} never reached the timeline"

    # Nothing the firm would not want written down.
    for event in activity:
        assert tenant.api_key not in event["summary"]


async def ingest_invoice(
    tenant: Tenant,
    *,
    case_id: str,
    client_id: str,
    filename: str,
    buyer: str | None,
    supplier: str | None,
) -> None:
    """Put an invoice through the pipeline, as the extraction stage does."""
    spec = InvoiceSpec(
        invoice_date="2026-09-14",
        buyer_gstin=buyer,
        supplier_gstin=supplier,
    )
    content = build_invoice_pdf(spec)

    from app.pipelines.stages.preprocess import prepare_document
    from app.services.file_validation import validate_upload

    validated = validate_upload(
        content,
        filename=filename,
        max_size_bytes=settings_max_size(),
        max_page_count=10,
    )
    async with get_session_factory()() as session:
        document = Document(
            organization_id=tenant.organization_id,
            client_id=client_id,
            case_id=case_id,
            filename=filename,
            content_type=validated.content_type,
            size_bytes=validated.size_bytes,
            page_count=validated.page_count,
            checksum_sha256=validated.checksum_sha256,
            source="upload",
        )
        session.add(document)
        await session.flush()
        await IngestionService(session).ingest(
            document=document,
            file=validated,
            prepared=prepare_document(validated),
            client=await session.get(Client, client_id),
            case=await session.get(ComplianceCase, case_id),
            invoice={
                "invoice_number": spec.invoice_number,
                "invoice_date": spec.invoice_date,
                "supplier": {"name": spec.supplier_name, "gstin": supplier},
                "buyer": {"name": spec.buyer_name, "gstin": buyer},
                "total": str(spec.total),
            },
            validation_passed=True,
        )
        await session.commit()


def settings_max_size() -> int:
    from app.core.config import get_settings

    return get_settings().max_file_size_bytes


async def recomputed(tenant: Tenant, case_id: str) -> str:
    async with get_session_factory()() as session:
        case = await CaseRepository(session).get(tenant.organization_id, case_id)
        status = await refresh_case_status(session, case)
        await session.commit()
    return status


# --- the parts of the journey that must not happen ----------------------


async def test_a_second_firm_sees_none_of_it(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    tenant: Tenant,
    other_tenant: Tenant,
    whatsapp: MockWhatsAppProvider,
) -> None:
    """One firm's client list, cases and exceptions are its own (§18)."""
    mine = data(
        await client.post(
            "/v1/clients",
            headers=auth_headers,
            json={"name": "Marigold Retail", "whatsapp_phone": PHONE},
        )
    )
    async with get_session_factory()() as session:
        theirs = await ClientRepository(session).create(
            other_tenant.organization_id, name="Rival's client"
        )
        await session.commit()

    listed = data(await client.get("/v1/clients", headers=auth_headers))
    assert [row["id"] for row in listed] == [mine["id"]]

    response = await client.get(f"/v1/clients/{theirs.id}", headers=auth_headers)
    assert response.status_code == 404

    response = await client.post(
        "/v1/cases",
        headers=auth_headers,
        json={"client_id": theirs.id, "type": "gst", "period": PERIOD},
    )
    assert response.status_code == 404


async def test_the_operations_api_refuses_an_api_key(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    """These endpoints are the dashboard's, not the integration's.

    An API key is for a program posting invoices. The client list is a
    person's view of their own practice, so it needs a signed-in session.
    """
    for path in ("/v1/clients", "/v1/command-centre", "/v1/exceptions", "/v1/tasks"):
        response = await client.get(path, headers=tenant.headers)
        assert response.status_code == 401, f"{path} accepted an API key"


async def test_a_case_cannot_be_opened_twice_for_the_same_period(
    client: httpx.AsyncClient, auth_headers: dict[str, str], tenant: Tenant
) -> None:
    person = data(
        await client.post("/v1/clients", headers=auth_headers, json={"name": "Marigold"})
    )
    body = {"client_id": person["id"], "type": "gst", "period": PERIOD}
    assert (await client.post("/v1/cases", headers=auth_headers, json=body)).status_code == 201

    duplicate = await client.post("/v1/cases", headers=auth_headers, json=body)
    assert duplicate.status_code == 400
    assert PERIOD in duplicate.json()["error"]["message"]


async def test_resolving_an_exception_lets_the_right_document_through(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    tenant: Tenant,
    whatsapp: MockWhatsAppProvider,
) -> None:
    """A stranger's invoice must not lock a requirement for ever.

    It arrives, it is wrong, it goes to review. Until the firm's decision
    released it, the client's own invoice arriving afterwards was turned away
    by a requirement nothing could ever satisfy again — the case could not be
    filed and nobody could see why.
    """
    person = data(
        await client.post(
            "/v1/clients",
            headers=auth_headers,
            json={"name": "Marigold Retail", "whatsapp_phone": PHONE, "gstin": GSTIN},
        )
    )
    case = data(
        await client.post(
            "/v1/cases",
            headers=auth_headers,
            json={"client_id": person["id"], "type": "gst", "period": PERIOD},
        )
    )

    await ingest_invoice(
        tenant,
        case_id=case["id"],
        client_id=person["id"],
        filename="not-theirs.pdf",
        buyer=SOMEBODY_ELSE,
        supplier="27AAACB2222B1ZJ",
    )
    held = await statuses(tenant, case["id"])
    assert held[DocumentType.SALES_INVOICE] == RequirementStatus.NEEDS_REVIEW

    exceptions = data(await client.get("/v1/exceptions", headers=auth_headers))
    mismatch = next(e for e in exceptions if e["type"] == ExceptionType.GSTIN_MISMATCH)
    resolved = data(
        await client.post(
            f"/v1/exceptions/{mismatch['id']}/resolve",
            headers=auth_headers,
            json={"status": ExceptionStatus.RESOLVED, "note": "Their brother's firm."},
        )
    )
    assert resolved["status"] == ExceptionStatus.RESOLVED

    released = await statuses(tenant, case["id"])
    assert released[DocumentType.SALES_INVOICE] == RequirementStatus.INVALID, (
        "the requirement is owed again, not stuck in review"
    )

    # And now the client's own invoice settles it.
    await ingest_invoice(
        tenant,
        case_id=case["id"],
        client_id=person["id"],
        filename="sept-sales.pdf",
        buyer=SOMEBODY_ELSE,
        supplier=GSTIN,
    )
    assert (await statuses(tenant, case["id"]))[
        DocumentType.SALES_INVOICE
    ] == RequirementStatus.VALID
