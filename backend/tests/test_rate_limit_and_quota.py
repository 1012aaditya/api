"""Rate limiting and monthly quota (§18)."""

from __future__ import annotations

import httpx
from sqlalchemy import select

from app.core.rate_limit import InMemoryRateLimiter, set_rate_limiter
from app.db.session import get_session_factory
from app.models import Organization, UsageEvent
from tests.conftest import StubProvider, Tenant
from tests.fixtures.invoices import build_invoice_pdf

ENDPOINT = "/v1/invoices/extract"


async def _set_limits(
    organization_id: str, *, per_minute: int | None = None, quota: int | None = None
) -> None:
    async with get_session_factory()() as session:
        organization = (
            await session.execute(
                select(Organization).where(Organization.id == organization_id)
            )
        ).scalar_one()
        if per_minute is not None:
            organization.rate_limit_per_minute = per_minute
        if quota is not None:
            organization.monthly_document_quota = quota
        await session.commit()


async def test_requests_past_the_limit_get_429(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    await _set_limits(tenant.organization_id, per_minute=3)
    set_rate_limiter(InMemoryRateLimiter())

    statuses = [
        (await client.get("/v1/documents", headers=tenant.headers)).status_code
        for _ in range(5)
    ]
    assert statuses[:3] == [200, 200, 200]
    assert statuses[3:] == [429, 429]


async def test_the_429_body_and_headers_match_the_documented_contract(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    await _set_limits(tenant.organization_id, per_minute=1)
    set_rate_limiter(InMemoryRateLimiter())

    await client.get("/v1/documents", headers=tenant.headers)
    response = await client.get("/v1/documents", headers=tenant.headers)

    assert response.status_code == 429
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "rate_limit_exceeded"
    assert "Rate limit" in body["error"]["message"]
    assert int(response.headers["Retry-After"]) >= 1


async def test_limits_are_per_organization(
    client: httpx.AsyncClient, tenant: Tenant, other_tenant: Tenant
) -> None:
    await _set_limits(tenant.organization_id, per_minute=1)
    set_rate_limiter(InMemoryRateLimiter())

    await client.get("/v1/documents", headers=tenant.headers)
    assert (await client.get("/v1/documents", headers=tenant.headers)).status_code == 429
    # A different tenant is unaffected.
    assert (
        await client.get("/v1/documents", headers=other_tenant.headers)
    ).status_code == 200


async def test_monthly_quota_blocks_extraction_with_403(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    await _set_limits(tenant.organization_id, quota=1)

    files = {"file": ("i.pdf", build_invoice_pdf(), "application/pdf")}
    first = await client.post(ENDPOINT, files=files, headers=tenant.headers)
    assert first.status_code == 200

    second = await client.post(
        ENDPOINT,
        files={"file": ("i.pdf", build_invoice_pdf(), "application/pdf")},
        headers=tenant.headers,
    )
    assert second.status_code == 403
    assert second.json()["error"]["code"] == "quota_exceeded"
    assert second.json()["error"]["details"]["quota"] == 1


async def test_quota_counts_only_billable_events(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    await _set_limits(tenant.organization_id, quota=2)

    # A rejected upload must not eat the allowance.
    await client.post(
        ENDPOINT,
        files={"file": ("x.gif", b"GIF89a" + b"\x00" * 40, "image/gif")},
        headers=tenant.headers,
    )
    for _ in range(2):
        response = await client.post(
            ENDPOINT,
            files={"file": ("i.pdf", build_invoice_pdf(), "application/pdf")},
            headers=tenant.headers,
        )
        assert response.status_code == 200

    async with get_session_factory()() as session:
        events = list((await session.execute(select(UsageEvent))).scalars())
    assert sum(1 for e in events if e.billable) == 2


async def test_the_limiter_fails_open_when_its_backend_is_down() -> None:
    """A limiter outage must not take the API down with it."""
    from app.core.rate_limit import RedisRateLimiter

    class BrokenRedis:
        def pipeline(self):
            raise ConnectionError("redis is gone")

    decision = await RedisRateLimiter(BrokenRedis()).check(
        "org:1", limit=10, window_seconds=60
    )
    assert decision.allowed is True


async def test_in_memory_limiter_rolls_over_between_windows() -> None:
    limiter = InMemoryRateLimiter()
    for _ in range(3):
        await limiter.check("k", limit=3, window_seconds=60)
    assert not (await limiter.check("k", limit=3, window_seconds=60)).allowed
    # A one-second window has certainly rolled over by the next call.
    assert (await limiter.check("k", limit=3, window_seconds=1)).allowed
