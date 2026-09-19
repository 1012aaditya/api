"""Classification, ingestion, and the exception engine.

The question every test here asks is the product's central one: after a file
arrives, does the system know what it is, and if it cannot be sure, does it
say so instead of guessing?
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db.session import get_session_factory
from app.models import (
    CaseStatus,
    Client,
    ComplianceCase,
    Document,
    DocumentRequirement,
    DocumentType,
    ExceptionStatus,
    ExceptionType,
    RequirementStatus,
    Severity,
)
from app.models.requirement import IllegalTransition
from app.pipelines.stages.preprocess import PreparedDocument
from app.repositories.clients import CaseRepository, ClientRepository, RequirementRepository
from app.repositories.operations import AgentEventRepository, ExceptionRepository
from app.services.classification import RulesClassifier
from app.services.ingestion import IngestionService, refresh_case_status
from tests.conftest import Tenant

CLIENT_GSTIN = "29AABCU9603R1ZJ"
OTHER_GSTIN = "27AAACA1111A1Z5"


def prepared(text: str) -> PreparedDocument:
    return PreparedDocument(embedded_text=text)


def invoice(
    *,
    buyer: str | None = None,
    supplier: str | None = None,
    date: str = "2026-09-14",
) -> dict:
    return {
        "invoice_number": "INV-1",
        "invoice_date": date,
        "supplier": {"name": "Acme Traders", "gstin": supplier},
        "buyer": {"name": "Marigold Retail", "gstin": buyer},
        "total": 118000,
    }


async def make_client(tenant: Tenant, **fields) -> Client:
    async with get_session_factory()() as session:
        client = await ClientRepository(session).create(
            tenant.organization_id,
            name=fields.pop("name", "ABC Traders"),
            gstin=fields.pop("gstin", CLIENT_GSTIN),
            whatsapp_phone=fields.pop("whatsapp_phone", "+919876543210"),
            **fields,
        )
        await session.commit()
        return client


async def make_case(tenant: Tenant, client_id: str, **fields) -> ComplianceCase:
    async with get_session_factory()() as session:
        case = await CaseRepository(session).create(
            organization_id=tenant.organization_id,
            client_id=client_id,
            case_type=fields.pop("case_type", "gst"),
            period=fields.pop("period", "2026-09"),
            deadline=fields.pop("deadline", dt.date(2026, 10, 20)),
            **fields,
        )
        await session.commit()
        return case


async def make_document(tenant: Tenant, *, filename: str, client_id=None, case_id=None) -> Document:
    async with get_session_factory()() as session:
        document = Document(
            organization_id=tenant.organization_id,
            filename=filename,
            content_type="application/pdf",
            size_bytes=1024,
            page_count=1,
            checksum_sha256="0" * 64,
            client_id=client_id,
            case_id=case_id,
            source="whatsapp",
        )
        session.add(document)
        await session.commit()
        return document


# --- the classifier ----------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "STATEMENT OF ACCOUNT\nIFSC HDFC0001234\nOpening Balance 1000",
            DocumentType.BANK_STATEMENT,
        ),
        ("GSTR-2B\nAuto-drafted\nITC available", DocumentType.GSTR_2B),
        ("CREDIT NOTE\nCr Note CN-1", DocumentType.CREDIT_NOTE),
        ("FORM 16A\nCertificate under section 203\nTRACES", DocumentType.TDS_CERTIFICATE),
        ("TAX INVOICE\nInvoice No: 1\nCGST SGST\nHSN 9983", DocumentType.SALES_INVOICE),
    ],
)
def test_the_classifier_reads_the_document(text: str, expected: str) -> None:
    result = RulesClassifier().classify(prepared(text))
    assert result.document_type == expected
    assert result.is_confident()
    assert result.evidence, "a confidence with no evidence is not reviewable"


def test_an_unreadable_document_is_not_guessed_at() -> None:
    result = RulesClassifier().classify(prepared(""), filename=None)
    assert result.document_type == DocumentType.OTHER
    assert result.confidence == 0.0
    assert not result.is_confident()


def test_a_filename_is_only_a_hint() -> None:
    """A filename alone must never reach the acting threshold."""
    result = RulesClassifier().classify(prepared(""), filename="bank statement.pdf")
    assert result.document_type == DocumentType.BANK_STATEMENT
    assert not result.is_confident(), "a filename is not evidence enough to act on"


def test_two_plausible_readings_lower_the_confidence() -> None:
    both = "CREDIT NOTE\nDEBIT NOTE"
    result = RulesClassifier().classify(prepared(both))
    assert result.confidence <= 0.70
    assert result.alternatives


# --- the requirement state machine -------------------------------------


def test_a_document_cannot_become_valid_without_arriving() -> None:
    requirement = DocumentRequirement(
        organization_id="org", case_id="case", document_type=DocumentType.BANK_STATEMENT
    )
    with pytest.raises(IllegalTransition):
        requirement.move_to(RequirementStatus.VALID)


def test_the_legal_path_records_its_timestamps() -> None:
    requirement = DocumentRequirement(
        organization_id="org", case_id="case", document_type=DocumentType.BANK_STATEMENT
    )
    requirement.move_to(RequirementStatus.REQUESTED)
    assert requirement.requested_at is not None
    requirement.move_to(RequirementStatus.RECEIVED)
    assert requirement.received_at is not None
    requirement.move_to(RequirementStatus.PROCESSING)
    requirement.move_to(RequirementStatus.VALID)
    assert requirement.settled_at is not None
    assert not requirement.is_outstanding


# --- creating a case ---------------------------------------------------


async def test_a_gst_case_asks_for_the_usual_documents(tenant: Tenant) -> None:
    client = await make_client(tenant)
    case = await make_case(tenant, client.id)

    async with get_session_factory()() as session:
        requirements = await RequirementRepository(session).for_case(
            tenant.organization_id, case.id
        )
    types = {r.document_type for r in requirements}
    assert DocumentType.BANK_STATEMENT in types
    assert DocumentType.GSTR_2B in types
    assert all(r.status == RequirementStatus.MISSING for r in requirements)
    # Credit notes are asked for but do not block.
    assert any(not r.required for r in requirements)


async def test_outstanding_lists_only_what_the_client_still_owes(tenant: Tenant) -> None:
    client = await make_client(tenant)
    case = await make_case(tenant, client.id)

    async with get_session_factory()() as session:
        repo = RequirementRepository(session)
        outstanding = await repo.outstanding_for_case(tenant.organization_id, case.id)
        assert len(outstanding) == 4  # the required ones

        bank = next(r for r in outstanding if r.document_type == DocumentType.BANK_STATEMENT)
        bank.move_to(RequirementStatus.RECEIVED)
        bank.move_to(RequirementStatus.PROCESSING)
        bank.move_to(RequirementStatus.VALID)
        await session.commit()

        assert len(await repo.outstanding_for_case(tenant.organization_id, case.id)) == 3


# --- ingestion ---------------------------------------------------------


async def test_a_bank_statement_settles_its_requirement(tenant: Tenant) -> None:
    client = await make_client(tenant)
    case = await make_case(tenant, client.id)
    document = await make_document(
        tenant, filename="sept-statement.pdf", client_id=client.id, case_id=case.id
    )

    async with get_session_factory()() as session:
        document = await session.get(Document, document.id)
        client_row = await session.get(Client, client.id)
        case_row = await session.get(ComplianceCase, case.id)
        outcome = await IngestionService(session).ingest(
            document=document,
            file=None,  # type: ignore[arg-type]
            prepared=prepared("STATEMENT OF ACCOUNT\nIFSC HDFC0001234\nOpening Balance 1,000"),
            client=client_row,
            case=case_row,
        )
        await session.commit()

    assert outcome.classification.document_type == DocumentType.BANK_STATEMENT
    assert outcome.requirement is not None
    assert outcome.requirement.status == RequirementStatus.VALID
    assert outcome.settled
    assert not outcome.needs_human


async def test_an_unrecognisable_document_goes_to_a_human(tenant: Tenant) -> None:
    client = await make_client(tenant)
    case = await make_case(tenant, client.id)
    document = await make_document(
        tenant, filename="scan_0042.pdf", client_id=client.id, case_id=case.id
    )

    async with get_session_factory()() as session:
        outcome = await IngestionService(session).ingest(
            document=await session.get(Document, document.id),
            file=None,  # type: ignore[arg-type]
            prepared=prepared("blurry nonsense"),
            client=await session.get(Client, client.id),
            case=await session.get(ComplianceCase, case.id),
        )
        await session.commit()

    assert outcome.needs_human
    assert outcome.requirement is None
    assert outcome.exceptions[0].type == ExceptionType.CLASSIFICATION_UNCERTAIN
    # Nothing was claimed about the document.
    assert not outcome.settled


async def test_a_document_the_case_did_not_ask_for_is_reported(tenant: Tenant) -> None:
    client = await make_client(tenant)
    case = await make_case(tenant, client.id)  # a GST case
    document = await make_document(
        tenant, filename="aadhaar.pdf", client_id=client.id, case_id=case.id
    )

    async with get_session_factory()() as session:
        outcome = await IngestionService(session).ingest(
            document=await session.get(Document, document.id),
            file=None,  # type: ignore[arg-type]
            prepared=prepared("AADHAAR\nUnique Identification Authority of India"),
            client=await session.get(Client, client.id),
            case=await session.get(ComplianceCase, case.id),
        )
        await session.commit()

    assert outcome.exceptions[0].type == ExceptionType.WRONG_DOCUMENT_TYPE
    assert "does not ask for" in outcome.exceptions[0].message


async def test_an_invoice_for_the_wrong_client_is_caught(tenant: Tenant) -> None:
    """The check that stops one client's document landing in another's books."""
    client = await make_client(tenant)
    case = await make_case(tenant, client.id)
    document = await make_document(
        tenant, filename="invoice.pdf", client_id=client.id, case_id=case.id
    )

    async with get_session_factory()() as session:
        outcome = await IngestionService(session).ingest(
            document=await session.get(Document, document.id),
            file=None,  # type: ignore[arg-type]
            prepared=prepared("TAX INVOICE\nInvoice No 1\nCGST SGST\nHSN 9983"),
            client=await session.get(Client, client.id),
            case=await session.get(ComplianceCase, case.id),
            invoice=invoice(buyer=OTHER_GSTIN, supplier="27AAACB2222B1Z3"),
            validation_passed=True,
        )
        await session.commit()

    mismatch = [e for e in outcome.exceptions if e.type == ExceptionType.GSTIN_MISMATCH]
    assert mismatch, "an invoice naming neither the client nor their supplier must be flagged"
    assert mismatch[0].severity == Severity.HIGH
    assert outcome.requirement.status == RequirementStatus.NEEDS_REVIEW


async def test_the_buyer_gstin_decides_a_purchase_from_a_sale(tenant: Tenant) -> None:
    """Nothing on the page says which side you are on — the GSTIN does."""
    client = await make_client(tenant)
    case = await make_case(tenant, client.id)
    document = await make_document(
        tenant, filename="invoice.pdf", client_id=client.id, case_id=case.id
    )

    async with get_session_factory()() as session:
        outcome = await IngestionService(session).ingest(
            document=await session.get(Document, document.id),
            file=None,  # type: ignore[arg-type]
            prepared=prepared("TAX INVOICE\nInvoice No 1\nCGST SGST\nHSN 9983"),
            client=await session.get(Client, client.id),
            case=await session.get(ComplianceCase, case.id),
            invoice=invoice(buyer=CLIENT_GSTIN, supplier=OTHER_GSTIN),
            validation_passed=True,
        )
        await session.commit()

    # The classifier said "sales invoice"; the GSTIN overruled it.
    assert outcome.classification.document_type == DocumentType.SALES_INVOICE
    assert outcome.requirement.document_type == DocumentType.PURCHASE_INVOICE
    assert any("bought it" in step for step in outcome.steps)


async def test_a_document_from_the_wrong_month_is_flagged(tenant: Tenant) -> None:
    client = await make_client(tenant)
    case = await make_case(tenant, client.id, period="2026-09")
    document = await make_document(
        tenant, filename="invoice.pdf", client_id=client.id, case_id=case.id
    )

    async with get_session_factory()() as session:
        outcome = await IngestionService(session).ingest(
            document=await session.get(Document, document.id),
            file=None,  # type: ignore[arg-type]
            prepared=prepared("TAX INVOICE\nInvoice No 1\nCGST SGST\nHSN 9983"),
            client=await session.get(Client, client.id),
            case=await session.get(ComplianceCase, case.id),
            invoice=invoice(buyer=CLIENT_GSTIN, supplier=OTHER_GSTIN, date="2026-07-02"),
            validation_passed=True,
        )
        await session.commit()

    assert any(e.type == ExceptionType.PERIOD_MISMATCH for e in outcome.exceptions)


async def test_an_unparsed_period_raises_nothing(tenant: Tenant) -> None:
    """A free-form period must not produce a confident wrong answer."""
    client = await make_client(tenant)
    case = await make_case(tenant, client.id, case_type="itr", period="FY 2025-26")
    document = await make_document(
        tenant, filename="statement.pdf", client_id=client.id, case_id=case.id
    )

    async with get_session_factory()() as session:
        outcome = await IngestionService(session).ingest(
            document=await session.get(Document, document.id),
            file=None,  # type: ignore[arg-type]
            prepared=prepared("STATEMENT OF ACCOUNT\nIFSC HDFC0001234\nOpening Balance 10"),
            client=await session.get(Client, client.id),
            case=await session.get(ComplianceCase, case.id),
            invoice=invoice(buyer=CLIENT_GSTIN, date="2020-01-01"),
            validation_passed=True,
        )
        await session.commit()

    assert not [e for e in outcome.exceptions if e.type == ExceptionType.PERIOD_MISMATCH]


async def test_failed_validation_holds_the_requirement(tenant: Tenant) -> None:
    client = await make_client(tenant)
    case = await make_case(tenant, client.id)
    document = await make_document(
        tenant, filename="invoice.pdf", client_id=client.id, case_id=case.id
    )

    async with get_session_factory()() as session:
        outcome = await IngestionService(session).ingest(
            document=await session.get(Document, document.id),
            file=None,  # type: ignore[arg-type]
            prepared=prepared("TAX INVOICE\nInvoice No 1\nCGST SGST\nHSN 9983"),
            client=await session.get(Client, client.id),
            case=await session.get(ComplianceCase, case.id),
            invoice=invoice(buyer=CLIENT_GSTIN, supplier=OTHER_GSTIN),
            validation_passed=False,
            validation_summary="The line items do not add up to the total.",
        )
        await session.commit()

    assert outcome.requirement.status == RequirementStatus.NEEDS_REVIEW
    assert any(e.type == ExceptionType.VALIDATION_FAILED for e in outcome.exceptions)
    assert "do not add up" in outcome.requirement.reason


# --- the exception engine ----------------------------------------------


async def test_the_same_finding_twice_is_one_exception(tenant: Tenant) -> None:
    async with get_session_factory()() as session:
        repo = ExceptionRepository(session)
        for _ in range(3):
            await repo.raise_exception(
                organization_id=tenant.organization_id,
                type=ExceptionType.GSTIN_MISMATCH,
                message="GSTIN does not match",
                dedupe_key="gstin_mismatch:doc_1",
            )
        await session.commit()
        assert await repo.open_count(tenant.organization_id) == 1


async def test_a_resolved_finding_that_returns_reopens(tenant: Tenant) -> None:
    async with get_session_factory()() as session:
        repo = ExceptionRepository(session)
        item = await repo.raise_exception(
            organization_id=tenant.organization_id,
            type=ExceptionType.GSTIN_MISMATCH,
            message="GSTIN does not match",
            dedupe_key="gstin_mismatch:doc_1",
        )
        await repo.resolve(item, status=ExceptionStatus.RESOLVED, note="fixed", user_id=None)
        await session.commit()

        again = await repo.raise_exception(
            organization_id=tenant.organization_id,
            type=ExceptionType.GSTIN_MISMATCH,
            message="GSTIN does not match",
            dedupe_key="gstin_mismatch:doc_1",
        )
        await session.commit()

    assert again.id == item.id
    assert again.status == ExceptionStatus.OPEN
    assert again.resolved_at is None


async def test_exceptions_do_not_leak_between_firms(
    tenant: Tenant, other_tenant: Tenant
) -> None:
    async with get_session_factory()() as session:
        await ExceptionRepository(session).raise_exception(
            organization_id=tenant.organization_id,
            type=ExceptionType.GSTIN_MISMATCH,
            message="mine",
            dedupe_key="k",
        )
        await session.commit()

    async with get_session_factory()() as session:
        assert await ExceptionRepository(session).open_count(other_tenant.organization_id) == 0


# --- case status is derived, never asserted ----------------------------


async def test_a_case_is_blocked_while_documents_are_outstanding(tenant: Tenant) -> None:
    client = await make_client(tenant)
    case = await make_case(tenant, client.id)

    async with get_session_factory()() as session:
        case_row = await session.get(ComplianceCase, case.id)
        assert await refresh_case_status(session, case_row) == CaseStatus.BLOCKED
        await session.commit()


async def test_a_case_becomes_ready_when_everything_is_settled(tenant: Tenant) -> None:
    client = await make_client(tenant)
    case = await make_case(tenant, client.id)

    async with get_session_factory()() as session:
        for requirement in await RequirementRepository(session).for_case(
            tenant.organization_id, case.id
        ):
            if not requirement.required:
                continue
            requirement.move_to(RequirementStatus.RECEIVED)
            requirement.move_to(RequirementStatus.PROCESSING)
            requirement.move_to(RequirementStatus.VALID)
        case_row = await session.get(ComplianceCase, case.id)
        assert await refresh_case_status(session, case_row) == CaseStatus.READY
        await session.commit()


async def test_an_open_high_severity_finding_keeps_a_case_off_ready(tenant: Tenant) -> None:
    client = await make_client(tenant)
    case = await make_case(tenant, client.id)

    async with get_session_factory()() as session:
        for requirement in await RequirementRepository(session).for_case(
            tenant.organization_id, case.id
        ):
            if not requirement.required:
                continue
            requirement.move_to(RequirementStatus.RECEIVED)
            requirement.move_to(RequirementStatus.PROCESSING)
            requirement.move_to(RequirementStatus.VALID)
        await ExceptionRepository(session).raise_exception(
            organization_id=tenant.organization_id,
            case_id=case.id,
            type=ExceptionType.GSTIN_MISMATCH,
            severity=Severity.HIGH,
            message="still unresolved",
            dedupe_key="k",
        )
        case_row = await session.get(ComplianceCase, case.id)
        assert await refresh_case_status(session, case_row) == CaseStatus.ESCALATED
        await session.commit()


# --- the audit trail ---------------------------------------------------


async def test_every_step_reaches_the_timeline(tenant: Tenant) -> None:
    client = await make_client(tenant)
    case = await make_case(tenant, client.id)
    document = await make_document(
        tenant, filename="statement.pdf", client_id=client.id, case_id=case.id
    )

    async with get_session_factory()() as session:
        await IngestionService(session).ingest(
            document=await session.get(Document, document.id),
            file=None,  # type: ignore[arg-type]
            prepared=prepared("STATEMENT OF ACCOUNT\nIFSC HDFC0001234\nOpening Balance 1"),
            client=await session.get(Client, client.id),
            case=await session.get(ComplianceCase, case.id),
        )
        await session.commit()

    async with get_session_factory()() as session:
        events = await AgentEventRepository(session).timeline(
            tenant.organization_id, client_id=client.id
        )
    actions = {event.action for event in events}
    assert "document.classified" in actions
    assert "requirement.updated" in actions
    assert all(event.summary for event in events), "an event nobody can read is not an audit trail"
