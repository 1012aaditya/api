"""Document storage, deletion and retention (§23, §24)."""

from __future__ import annotations

import datetime as dt

import httpx
from sqlalchemy import select

from app.db.base import utcnow
from app.db.session import get_session_factory
from app.models import Document
from app.repositories.documents import DocumentRepository
from tests.conftest import StubProvider, Tenant
from tests.fixtures.invoices import build_invoice_pdf

ENDPOINT = "/v1/invoices/extract"


async def _extract(client: httpx.AsyncClient, tenant: Tenant) -> dict:
    response = await client.post(
        ENDPOINT,
        files={"file": ("i.pdf", build_invoice_pdf(), "application/pdf")},
        headers=tenant.headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_retention_deadline_is_set_on_upload(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    await _extract(client, tenant)
    async with get_session_factory()() as session:
        document = (await session.execute(select(Document))).scalar_one()
    assert document.retention_expires_at is not None
    # The test environment configures 7 days.
    assert 6 <= (document.retention_expires_at - utcnow()).days <= 7


async def test_deleting_a_document_removes_the_bytes_but_keeps_the_record(
    client: httpx.AsyncClient,
    tenant: Tenant,
    use_provider,
    stub_provider: StubProvider,
    object_store,
) -> None:
    use_provider(stub_provider)
    document_id = (await _extract(client, tenant))["document_id"]
    assert len(object_store.objects) == 1

    response = await client.delete(f"/v1/documents/{document_id}", headers=tenant.headers)
    assert response.status_code == 200
    assert response.json()["data"]["stored"] is False
    assert response.json()["data"]["purged_at"] is not None

    assert object_store.objects == {}
    # The metadata row survives, so usage history and support lookups still work.
    still_there = await client.get(f"/v1/documents/{document_id}", headers=tenant.headers)
    assert still_there.status_code == 200
    assert still_there.json()["data"]["status"] == "purged"


async def test_deleting_twice_is_safe(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    document_id = (await _extract(client, tenant))["document_id"]
    first = await client.delete(f"/v1/documents/{document_id}", headers=tenant.headers)
    second = await client.delete(f"/v1/documents/{document_id}", headers=tenant.headers)
    assert first.status_code == second.status_code == 200


async def test_process_and_delete_never_stores_the_bytes(
    client: httpx.AsyncClient,
    tenant: Tenant,
    use_provider,
    stub_provider: StubProvider,
    object_store,
) -> None:
    """DOCUMENT_RETENTION_DAYS=0 means the document is never written down."""
    from sqlalchemy import select as sa_select

    from app.models import Organization

    async with get_session_factory()() as session:
        organization = (
            await session.execute(
                sa_select(Organization).where(Organization.id == tenant.organization_id)
            )
        ).scalar_one()
        organization.retention_days = 0
        await session.commit()

    use_provider(stub_provider)
    body = await _extract(client, tenant)

    assert object_store.objects == {}
    assert body["data"]["invoice_number"] == "INV-29381"  # still extracted
    async with get_session_factory()() as session:
        document = (await session.execute(select(Document))).scalar_one()
    assert document.storage_key is None
    assert document.retention_expires_at is None


async def test_the_sweeper_finds_only_expired_unpurged_documents(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    await _extract(client, tenant)

    async with get_session_factory()() as session:
        repo = DocumentRepository(session)
        now = utcnow()
        assert await repo.list_expired(now=now) == []

        document = (await session.execute(select(Document))).scalar_one()
        document.retention_expires_at = now - dt.timedelta(seconds=1)
        await session.commit()

        expired = await repo.list_expired(now=utcnow())
        assert [d.id for d in expired] == [document.id]


async def test_the_sweeper_deletes_expired_bytes(
    client: httpx.AsyncClient,
    tenant: Tenant,
    use_provider,
    stub_provider: StubProvider,
    object_store,
) -> None:
    from app.services.retention import purge_expired_documents

    use_provider(stub_provider)
    await _extract(client, tenant)
    assert len(object_store.objects) == 1

    async with get_session_factory()() as session:
        document = (await session.execute(select(Document))).scalar_one()
        document.retention_expires_at = utcnow() - dt.timedelta(days=1)
        await session.commit()

    purged = await purge_expired_documents(object_store=object_store)
    assert purged == 1
    assert object_store.objects == {}

    async with get_session_factory()() as session:
        document = (await session.execute(select(Document))).scalar_one()
    assert document.purged_at is not None
    assert document.storage_key is None
    # Running again is a no-op, not a second deletion.
    assert await purge_expired_documents(object_store=object_store) == 0
