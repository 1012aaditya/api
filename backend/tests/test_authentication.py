"""API key authentication (§5, §19)."""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import Tenant

PROTECTED = "/v1/documents"


async def test_missing_authorization_header(client: httpx.AsyncClient) -> None:
    response = await client.get(PROTECTED)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.parametrize(
    "header",
    ["", "Bearer", "Bearer ", "Basic dXNlcjpwYXNz", "dp_live_abc", "Token dp_live_abc"],
)
async def test_malformed_authorization_headers(
    client: httpx.AsyncClient, header: str
) -> None:
    response = await client.get(PROTECTED, headers={"Authorization": header})
    assert response.status_code == 401
    assert response.json()["error"]["code"] in {
        "authentication_required",
        "invalid_api_key",
    }


@pytest.mark.parametrize(
    "token",
    ["dp_live_totally-made-up", "dp_test_totally-made-up", "sk-openai-style-key", "x" * 80],
)
async def test_unknown_or_wrong_shaped_keys_are_rejected(
    client: httpx.AsyncClient, token: str
) -> None:
    response = await client.get(PROTECTED, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


async def test_a_valid_key_is_accepted(client: httpx.AsyncClient, tenant: Tenant) -> None:
    assert (await client.get(PROTECTED, headers=tenant.headers)).status_code == 200


async def test_failed_auth_never_echoes_the_key(client: httpx.AsyncClient) -> None:
    secret = "dp_live_this-should-never-be-echoed-back"
    response = await client.get(PROTECTED, headers={"Authorization": f"Bearer {secret}"})
    assert secret not in response.text


async def test_health_endpoints_stay_public(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/ready")).status_code == 200
