"""Tenant isolation (§16).

Every customer-facing read is scoped to one organization. A resource
belonging to another tenant is reported as absent, not forbidden — "403" on
an id you do not own confirms that the id exists.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.db.session import get_session_factory
from app.models import Document, Extraction, UsageEvent
from app.repositories.documents import DocumentRepository
from tests.conftest import StubProvider, Tenant
from tests.fixtures.invoices import build_invoice_pdf

ENDPOINT = "/v1/invoices/extract"


async def _extract_as(client: httpx.AsyncClient, tenant: Tenant) -> dict:
    response = await client.post(
        ENDPOINT,
        files={"file": ("i.pdf", build_invoice_pdf(), "application/pdf")},
        headers=tenant.headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_document_is_invisible_to_another_tenant(
    client: httpx.AsyncClient,
    tenant: Tenant,
    other_tenant: Tenant,
    use_provider,
    stub_provider: StubProvider,
) -> None:
    use_provider(stub_provider)
    body = await _extract_as(client, tenant)
    document_id = body["document_id"]

    mine = await client.get(f"/v1/documents/{document_id}", headers=tenant.headers)
    theirs = await client.get(f"/v1/documents/{document_id}", headers=other_tenant.headers)

    assert mine.status_code == 200
    assert theirs.status_code == 404
    assert theirs.json()["error"]["code"] == "not_found"


async def test_listing_documents_only_shows_your_own(
    client: httpx.AsyncClient,
    tenant: Tenant,
    other_tenant: Tenant,
    use_provider,
    stub_provider: StubProvider,
) -> None:
    use_provider(stub_provider)
    await _extract_as(client, tenant)
    await _extract_as(client, other_tenant)

    mine = (await client.get("/v1/documents", headers=tenant.headers)).json()["data"]
    theirs = (await client.get("/v1/documents", headers=other_tenant.headers)).json()["data"]

    assert len(mine) == 1
    assert len(theirs) == 1
    assert mine[0]["id"] != theirs[0]["id"]


async def test_another_tenant_cannot_delete_your_document(
    client: httpx.AsyncClient,
    tenant: Tenant,
    other_tenant: Tenant,
    use_provider,
    stub_provider: StubProvider,
) -> None:
    use_provider(stub_provider)
    document_id = (await _extract_as(client, tenant))["document_id"]

    response = await client.delete(
        f"/v1/documents/{document_id}", headers=other_tenant.headers
    )
    assert response.status_code == 404

    # Still there, and still ours.
    still_mine = await client.get(f"/v1/documents/{document_id}", headers=tenant.headers)
    assert still_mine.status_code == 200
    assert still_mine.json()["data"]["stored"] is True


async def test_another_tenant_cannot_see_or_revoke_your_api_keys(
    client: httpx.AsyncClient, tenant: Tenant, auth_headers: dict[str, str]
) -> None:
    from tests.conftest import create_tenant

    intruder = await create_tenant("Intruder Ltd")
    login = await client.post(
        "/v1/auth/login", json={"email": intruder.email, "password": intruder.password}
    )
    intruder_headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    listed = (await client.get("/v1/api-keys", headers=intruder_headers)).json()["data"]
    assert all(key["id"] != tenant.api_key_id for key in listed)

    response = await client.delete(
        f"/v1/api-keys/{tenant.api_key_id}", headers=intruder_headers
    )
    assert response.status_code == 404


async def test_stored_rows_carry_the_owning_organization(
    client: httpx.AsyncClient,
    tenant: Tenant,
    other_tenant: Tenant,
    use_provider,
    stub_provider: StubProvider,
) -> None:
    use_provider(stub_provider)
    await _extract_as(client, tenant)
    await _extract_as(client, other_tenant)

    async with get_session_factory()() as session:
        for model in (Document, Extraction, UsageEvent):
            rows = list((await session.execute(select(model))).scalars())
            owners = {row.organization_id for row in rows}
            assert owners == {tenant.organization_id, other_tenant.organization_id}


async def test_the_repository_itself_refuses_a_cross_tenant_read(
    client: httpx.AsyncClient,
    tenant: Tenant,
    other_tenant: Tenant,
    use_provider,
    stub_provider: StubProvider,
) -> None:
    """Isolation is enforced in the data layer, not only in the route."""
    use_provider(stub_provider)
    document_id = (await _extract_as(client, tenant))["document_id"]

    async with get_session_factory()() as session:
        repo = DocumentRepository(session)
        assert await repo.get(tenant.organization_id, document_id) is not None
        assert await repo.get(other_tenant.organization_id, document_id) is None


def test_document_repository_requires_an_organization_id() -> None:
    """There is no unscoped get() to reach for by accident."""
    with pytest.raises(TypeError):
        DocumentRepository(None).get("doc_123")  # type: ignore[call-arg]
