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


async def _set_usage(organization_id: str, *, count: int) -> None:
    """Put an organization this far into the month, without the documents.

    Billable usage events are what the allowance counts, so writing them
    directly gets to the interesting state without processing hundreds of
    invoices to reach it.
    """
    from app.repositories.usage import UsageRepository

    async with get_session_factory()() as session:
        repository = UsageRepository(session)
        for _ in range(count):
            await repository.record(
                organization_id=organization_id,
                endpoint="/v1/invoices/extract",
                status_code=200,
                success=True,
                billable=True,
            )
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


async def test_an_explicit_ceiling_blocks_extraction_with_403(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    """An operator who set monthly_document_quota meant it, so it stands as
    the ceiling for that organization."""
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
    assert second.json()["error"]["details"]["ceiling"] == 1


async def test_passing_the_plan_allowance_does_not_stop_the_work(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    """The behaviour this exists for. A firm on the 18th with a filing due
    on the 20th cannot retry tomorrow, so the allowance bills rather than
    blocks — the ceiling is a separate, far higher number."""
    from app.services.plans import get_plan

    use_provider(stub_provider)
    plan = get_plan("trial")
    # One document past what the plan includes, and far below the ceiling.
    await _set_usage(tenant.organization_id, count=plan.included_documents + 1)

    response = await client.post(
        ENDPOINT,
        files={"file": ("i.pdf", build_invoice_pdf(), "application/pdf")},
        headers=tenant.headers,
    )

    assert response.status_code == 200, (
        "a firm past its allowance was blocked; it should have been billed"
    )


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


async def test_in_memory_limiter_rolls_over_between_windows(monkeypatch) -> None:
    """Time is driven, not waited on — a timing-dependent test is a flaky test."""
    import app.core.rate_limit as rate_limit

    now = [1_000_000.0]
    monkeypatch.setattr(rate_limit.time, "time", lambda: now[0])

    limiter = InMemoryRateLimiter()
    for _ in range(3):
        assert (await limiter.check("k", limit=3, window_seconds=60)).allowed
    blocked = await limiter.check("k", limit=3, window_seconds=60)
    assert not blocked.allowed
    assert blocked.remaining == 0
    assert blocked.retry_after_seconds >= 1

    now[0] += 60  # into the next window
    assert (await limiter.check("k", limit=3, window_seconds=60)).allowed
