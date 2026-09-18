"""Account creation and dashboard sessions."""

from __future__ import annotations

import httpx

from tests.conftest import Tenant


async def test_signup_creates_a_user_and_an_organization(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/auth/signup",
        json={
            "email": "founder@example.com",
            "password": "correct-horse-battery-staple",
            "organization_name": "Startup Books",
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["access_token"]
    assert data["user"]["email"] == "founder@example.com"
    assert data["user"]["organization"]["name"] == "Startup Books"
    assert data["user"]["organization"]["id"].startswith("org_")


async def test_signup_rejects_a_duplicate_email(client: httpx.AsyncClient) -> None:
    payload = {"email": "dupe@example.com", "password": "correct-horse-battery-staple"}
    assert (await client.post("/v1/auth/signup", json=payload)).status_code == 201
    response = await client.post("/v1/auth/signup", json=payload)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_signup_rejects_a_short_password(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/auth/signup", json={"email": "weak@example.com", "password": "short"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


async def test_login_returns_a_usable_session_token(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    response = await client.post(
        "/v1/auth/login", json={"email": tenant.email, "password": tenant.password}
    )
    assert response.status_code == 200
    token = response.json()["data"]["access_token"]

    me = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["data"]["organization"]["id"] == tenant.organization_id


async def test_wrong_password_and_unknown_email_are_indistinguishable(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    wrong_password = await client.post(
        "/v1/auth/login", json={"email": tenant.email, "password": "not-the-password"}
    )
    unknown_email = await client.post(
        "/v1/auth/login",
        json={"email": "nobody@nowhere.example.com", "password": "not-the-password"},
    )
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert (
        wrong_password.json()["error"]["message"]
        == unknown_email.json()["error"]["message"]
    )


async def test_password_is_never_returned_or_stored_in_the_clear(
    client: httpx.AsyncClient,
) -> None:
    from sqlalchemy import select

    from app.db.session import get_session_factory
    from app.models import User

    password = "correct-horse-battery-staple"
    response = await client.post(
        "/v1/auth/signup", json={"email": "hash@example.com", "password": password}
    )
    assert password not in response.text

    async with get_session_factory()() as session:
        user = (
            await session.execute(select(User).where(User.email == "hash@example.com"))
        ).scalar_one()
    assert user.password_hash != password
    assert user.password_hash.startswith("$2b$")


async def test_me_rejects_an_api_key(client: httpx.AsyncClient, tenant: Tenant) -> None:
    """A leaked API key must not unlock the dashboard session endpoints."""
    response = await client.get("/v1/auth/me", headers=tenant.headers)
    assert response.status_code == 401
