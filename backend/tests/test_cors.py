"""The browser's preflight has to pass for every method the API routes.

A method missing from ``allow_methods`` does not fail on the server: the
request never arrives. The dashboard sees a network error with no status and
no request id, which is the least debuggable failure the stack can produce —
so this is checked here rather than found in a browser.
"""

from __future__ import annotations

import pytest

ORIGIN = "https://dashboard.example.com"


def routed_methods(app) -> set[str]:
    methods: set[str] = set()
    for route in app.routes:
        methods |= set(getattr(route, "methods", set()) or set())
    return methods - {"HEAD", "OPTIONS"}


def test_every_routed_method_survives_a_preflight(app):
    for method in sorted(routed_methods(app)):
        assert method in {"GET", "POST", "PATCH", "PUT", "DELETE"}, (
            f"{method} is routed but is not one of the methods CORS allows"
        )


@pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "PUT", "DELETE"])
async def test_the_preflight_allows_the_method(client, method):
    response = await client.options(
        "/v1/clients",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200, response.text
    allowed = response.headers["access-control-allow-methods"]
    assert method in allowed


async def test_the_preflight_allows_the_headers_the_dashboard_sends(client):
    response = await client.options(
        "/v1/agent/policy",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed
    assert "content-type" in allowed
