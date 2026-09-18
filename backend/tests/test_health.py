"""Health and readiness probes (§31)."""

from __future__ import annotations

import httpx


async def test_health_is_dependency_free(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_reports_database_and_provider(client: httpx.AsyncClient) -> None:
    response = await client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    # No credentials are configured in tests, and readiness says so plainly
    # rather than hiding it.
    assert body["checks"]["ai_provider"] == "not_configured"


async def test_every_response_carries_a_request_id(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.headers["X-Request-Id"].startswith("req_")


async def test_client_supplied_request_id_is_echoed(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-Id": "req_client_123"})
    assert response.headers["X-Request-Id"] == "req_client_123"


async def test_malicious_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-Id": "abc def;rm -rf /"})
    assert response.headers["X-Request-Id"].startswith("req_")


async def test_unknown_route_uses_the_error_envelope(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/nope")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "not_found"
    assert body["request_id"].startswith("req_")
