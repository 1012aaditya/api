"""API key lifecycle (§17)."""

from __future__ import annotations

import httpx
from sqlalchemy import select

from app.db.session import get_session_factory
from app.models import APIKey
from tests.conftest import Tenant


async def test_created_key_is_returned_once_and_stored_hashed(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/v1/api-keys", json={"name": "CI key"}, headers=auth_headers
    )
    assert response.status_code == 201, response.text
    created = response.json()["data"]
    plaintext = created["key"]
    assert plaintext.startswith("dp_live_")

    async with get_session_factory()() as session:
        row = (
            await session.execute(select(APIKey).where(APIKey.id == created["id"]))
        ).scalar_one()
    # The secret itself is nowhere in the row.
    assert plaintext not in (row.key_hash, row.prefix, row.last_four)
    assert len(row.key_hash) == 64

    listed = await client.get("/v1/api-keys", headers=auth_headers)
    assert plaintext not in listed.text
    assert created["masked_key"] in listed.text


async def test_a_created_key_authenticates_api_requests(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = (
        await client.post("/v1/api-keys", json={"name": "k"}, headers=auth_headers)
    ).json()["data"]
    response = await client.get(
        "/v1/documents", headers={"Authorization": f"Bearer {created['key']}"}
    )
    assert response.status_code == 200


async def test_revoked_key_stops_working(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = (
        await client.post("/v1/api-keys", json={"name": "k"}, headers=auth_headers)
    ).json()["data"]
    key_headers = {"Authorization": f"Bearer {created['key']}"}
    assert (await client.get("/v1/documents", headers=key_headers)).status_code == 200

    revoked = await client.delete(f"/v1/api-keys/{created['id']}", headers=auth_headers)
    assert revoked.status_code == 200
    assert revoked.json()["data"]["active"] is False

    response = await client.get("/v1/documents", headers=key_headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


async def test_rotation_issues_a_new_key_and_retires_the_old_one(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    original = (
        await client.post("/v1/api-keys", json={"name": "prod"}, headers=auth_headers)
    ).json()["data"]

    rotated = await client.post(
        f"/v1/api-keys/{original['id']}/rotate", headers=auth_headers
    )
    assert rotated.status_code == 200
    replacement = rotated.json()["data"]
    assert replacement["key"] != original["key"]
    assert replacement["name"] == "prod"

    old = await client.get(
        "/v1/documents", headers={"Authorization": f"Bearer {original['key']}"}
    )
    new = await client.get(
        "/v1/documents", headers={"Authorization": f"Bearer {replacement['key']}"}
    )
    assert old.status_code == 401
    assert new.status_code == 200


async def test_rotating_an_already_revoked_key_is_refused(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = (
        await client.post("/v1/api-keys", json={"name": "k"}, headers=auth_headers)
    ).json()["data"]
    await client.delete(f"/v1/api-keys/{created['id']}", headers=auth_headers)
    response = await client.post(
        f"/v1/api-keys/{created['id']}/rotate", headers=auth_headers
    )
    assert response.status_code == 409


async def test_expired_key_is_rejected(client: httpx.AsyncClient, tenant: Tenant) -> None:
    import datetime as dt

    from app.db.base import utcnow
    from app.repositories.api_keys import APIKeyRepository

    async with get_session_factory()() as session:
        row = await APIKeyRepository(session).get(tenant.organization_id, tenant.api_key_id)
        assert row is not None
        row.expires_at = utcnow() - dt.timedelta(seconds=1)
        await session.commit()

    response = await client.get("/v1/documents", headers=tenant.headers)
    assert response.status_code == 401


async def test_last_used_at_is_recorded(client: httpx.AsyncClient, tenant: Tenant) -> None:
    await client.get("/v1/documents", headers=tenant.headers)
    async with get_session_factory()() as session:
        row = (
            await session.execute(select(APIKey).where(APIKey.id == tenant.api_key_id))
        ).scalar_one()
    assert row.last_used_at is not None


async def test_api_key_endpoints_require_a_session_not_a_key(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    """An API key cannot mint further API keys."""
    response = await client.post("/v1/api-keys", json={"name": "x"}, headers=tenant.headers)
    assert response.status_code == 401
