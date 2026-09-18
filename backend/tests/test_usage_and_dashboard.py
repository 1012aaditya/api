"""Usage reporting and dual-credential access for the dashboard (§21, §25)."""

from __future__ import annotations

import httpx

from tests.conftest import StubProvider, Tenant
from tests.fixtures.invoices import build_invoice_pdf

ENDPOINT = "/v1/invoices/extract"


async def _extract(client: httpx.AsyncClient, headers: dict[str, str]) -> httpx.Response:
    return await client.post(
        ENDPOINT,
        files={"file": ("i.pdf", build_invoice_pdf(), "application/pdf")},
        headers=headers,
    )


# --- dual credentials --------------------------------------------------


async def test_read_endpoints_accept_a_session_token(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """The dashboard holds a session, not a key — keys are stored hashed."""
    for path in ("/v1/documents", "/v1/usage", "/v1/usage/events"):
        response = await client.get(path, headers=auth_headers)
        assert response.status_code == 200, f"{path}: {response.text}"


async def test_read_endpoints_still_accept_an_api_key(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    for path in ("/v1/documents", "/v1/usage", "/v1/usage/events"):
        assert (await client.get(path, headers=tenant.headers)).status_code == 200


async def test_usage_is_scoped_to_the_callers_organization(
    client: httpx.AsyncClient,
    tenant: Tenant,
    other_tenant: Tenant,
    use_provider,
    stub_provider: StubProvider,
) -> None:
    use_provider(stub_provider)
    await _extract(client, tenant.headers)
    await _extract(client, other_tenant.headers)
    await _extract(client, other_tenant.headers)

    mine = (await client.get("/v1/usage", headers=tenant.headers)).json()["data"]
    theirs = (await client.get("/v1/usage", headers=other_tenant.headers)).json()["data"]
    assert mine["totals"]["requests"] == 1
    assert theirs["totals"]["requests"] == 2


async def test_a_session_driven_extraction_is_tagged_as_such(
    client: httpx.AsyncClient, auth_headers: dict[str, str], use_provider,
    stub_provider: StubProvider,
) -> None:
    """Playground runs are real extractions and cost real money — but the
    audit trail should say they came from the dashboard, not from a key."""
    use_provider(stub_provider)
    assert (await _extract(client, auth_headers)).status_code == 200

    events = (await client.get("/v1/usage/events", headers=auth_headers)).json()["data"]
    assert events[0]["event_type"] == "dashboard_request"
    assert events[0]["billable"] is True


async def test_an_api_key_driven_extraction_is_tagged_as_api_traffic(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    await _extract(client, tenant.headers)
    events = (await client.get("/v1/usage/events", headers=tenant.headers)).json()["data"]
    assert events[0]["event_type"] == "api_request"


# --- the numbers -------------------------------------------------------


async def test_usage_reports_totals_quota_and_a_daily_series(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    await _extract(client, tenant.headers)
    await _extract(client, tenant.headers)

    data = (await client.get("/v1/usage", headers=tenant.headers)).json()["data"]
    assert data["totals"]["requests"] == 2
    assert data["totals"]["successful_requests"] == 2
    assert data["totals"]["failed_requests"] == 0
    assert data["totals"]["pages"] == 2
    assert data["totals"]["average_duration_ms"] is not None
    assert data["success_rate"] == 1.0

    assert data["quota"]["documents_used"] == 2
    assert data["quota"]["monthly_quota"] == 100
    assert data["quota"]["remaining"] == 98
    assert data["quota"]["rate_limit_per_minute"] == 60

    # The window is filled end to end, so only one day carries the traffic.
    busy = [point for point in data["daily"] if point["requests"] > 0]
    assert len(busy) == 1
    assert busy[0]["requests"] == 2
    assert busy[0]["documents"] == 2


async def test_success_rate_is_null_rather_than_zero_with_no_traffic(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    data = (await client.get("/v1/usage", headers=tenant.headers)).json()["data"]
    assert data["totals"]["requests"] == 0
    # A success rate over nothing is not a number worth showing.
    assert data["success_rate"] is None


async def test_failed_requests_are_counted_too(
    client: httpx.AsyncClient, tenant: Tenant, use_provider
) -> None:
    from app.core.errors import ExtractionFailedError

    use_provider(StubProvider(raises=ExtractionFailedError()))
    await _extract(client, tenant.headers)

    data = (await client.get("/v1/usage", headers=tenant.headers)).json()["data"]
    assert data["totals"]["failed_requests"] == 1
    assert data["success_rate"] == 0.0

    events = (await client.get("/v1/usage/events", headers=tenant.headers)).json()["data"]
    assert events[0]["error_code"] == "extraction_failed"
    assert events[0]["status_code"] == 422


# --- stored extractions ------------------------------------------------


async def test_a_stored_extraction_can_be_fetched_back(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    body = (await _extract(client, tenant.headers)).json()

    stored = await client.get(
        f"/v1/documents/{body['document_id']}/extraction", headers=tenant.headers
    )
    assert stored.status_code == 200
    data = stored.json()["data"]
    assert data["id"] == body["extraction_id"]
    assert data["status"] == "succeeded"
    assert data["data"]["invoice_number"] == "INV-29381"
    assert data["validation"]["overall"] == "passed"
    assert data["confidence"]["total"]["band"] == "high"


async def test_fetching_an_extraction_for_another_tenants_document_is_a_404(
    client: httpx.AsyncClient,
    tenant: Tenant,
    other_tenant: Tenant,
    use_provider,
    stub_provider: StubProvider,
) -> None:
    use_provider(stub_provider)
    document_id = (await _extract(client, tenant.headers)).json()["document_id"]
    response = await client.get(
        f"/v1/documents/{document_id}/extraction", headers=other_tenant.headers
    )
    assert response.status_code == 404


async def test_a_document_with_no_extraction_yet_is_a_404(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    response = await client.get(
        "/v1/documents/doc_does_not_exist/extraction", headers=tenant.headers
    )
    assert response.status_code == 404


async def test_a_failed_extraction_is_still_retrievable(
    client: httpx.AsyncClient, tenant: Tenant, use_provider
) -> None:
    from app.core.errors import ExtractionFailedError

    use_provider(StubProvider(raises=ExtractionFailedError("model returned prose")))
    await _extract(client, tenant.headers)

    documents = (await client.get("/v1/documents", headers=tenant.headers)).json()["data"]
    stored = (
        await client.get(
            f"/v1/documents/{documents[0]['id']}/extraction", headers=tenant.headers
        )
    ).json()["data"]
    assert stored["status"] == "failed"
    assert stored["error_code"] == "extraction_failed"
    assert stored["data"] is None


async def test_a_rejected_upload_is_recorded_but_not_billed(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    """A 415 is still a request the caller made.

    Leaving it out of the log makes "my uploads keep failing" undebuggable
    from the dashboard — the developer sees an empty request list.
    """
    use_provider(stub_provider)
    response = await client.post(
        ENDPOINT,
        files={"file": ("x.gif", b"GIF89a" + b"\x00" * 60, "image/gif")},
        headers=tenant.headers,
    )
    assert response.status_code == 415

    events = (await client.get("/v1/usage/events", headers=tenant.headers)).json()["data"]
    assert len(events) == 1
    assert events[0]["status_code"] == 415
    assert events[0]["error_code"] == "unsupported_file_type"
    assert events[0]["success"] is False
    # It cost us nothing, so it must not consume the monthly allowance.
    assert events[0]["billable"] is False

    usage = (await client.get("/v1/usage", headers=tenant.headers)).json()["data"]
    assert usage["totals"]["requests"] == 1
    assert usage["totals"]["failed_requests"] == 1
    assert usage["quota"]["documents_used"] == 0


async def test_an_oversized_upload_is_recorded_too(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    oversized = b"%PDF-1.4" + b"\x00" * (6 * 1024 * 1024)
    assert (
        await client.post(
            ENDPOINT,
            files={"file": ("big.pdf", oversized, "application/pdf")},
            headers=tenant.headers,
        )
    ).status_code == 413

    events = (await client.get("/v1/usage/events", headers=tenant.headers)).json()["data"]
    assert events[0]["error_code"] == "file_too_large"
    assert events[0]["billable"] is False


async def test_the_daily_series_covers_every_day_in_the_window(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    """Days with no traffic must be present as zeroes.

    A series that omits empty days puts non-adjacent dates next to each other
    and misstates the shape of the traffic.
    """
    use_provider(stub_provider)
    await _extract(client, tenant.headers)

    data = (await client.get("/v1/usage?days=7", headers=tenant.headers)).json()["data"]
    assert len(data["daily"]) == 8  # 7 days back, inclusive of both ends

    days = [point["day"] for point in data["daily"]]
    assert days == sorted(days)
    assert len(set(days)) == len(days)
    # Exactly one day carries today's traffic; the rest are real zeroes.
    with_traffic = [p for p in data["daily"] if p["requests"] > 0]
    assert len(with_traffic) == 1
    assert sum(p["requests"] for p in data["daily"]) == 1


async def test_a_stored_extraction_records_its_prompt_version(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    """A prompt change is a behaviour change; it has to be attributable (§33)."""
    use_provider(stub_provider)
    body = (await _extract(client, tenant.headers)).json()
    stored = (
        await client.get(
            f"/v1/documents/{body['document_id']}/extraction", headers=tenant.headers
        )
    ).json()["data"]
    assert stored["prompt_version"] == "gst_invoice.v1"
    assert stored["prompt_version"] == body["processing"]["prompt_version"]
