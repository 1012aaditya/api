"""Webhook registration, signing and delivery (§22, §23)."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.db.session import get_session_factory
from app.models import Webhook, WebhookDelivery
from app.services.webhook_security import (
    SIGNATURE_HEADER,
    UnsafeWebhookURLError,
    assert_destination_is_safe,
    derive_secret,
    sign_request,
    verify_request,
)
from app.services.webhooks import backoff_seconds, deliver_due
from app.workers.worker import process_available_jobs
from tests.conftest import StubProvider, Tenant
from tests.fixtures.invoices import build_invoice_pdf


async def register(
    client: httpx.AsyncClient, headers: dict[str, str], url: str, **kwargs
) -> dict:
    response = await client.post(
        "/v1/webhooks",
        json={"url": url, "events": ["document.completed", "document.failed"], **kwargs},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def submit(client: httpx.AsyncClient, tenant: Tenant) -> dict:
    response = await client.post(
        "/v1/documents",
        files={"file": ("i.pdf", build_invoice_pdf(), "application/pdf")},
        headers=tenant.headers,
    )
    assert response.status_code == 202, response.text
    return response.json()


# --- registration ------------------------------------------------------


async def test_registering_returns_the_secret_once(
    client: httpx.AsyncClient, auth_headers: dict[str, str], public_dns: str
) -> None:
    created = await register(client, auth_headers, public_dns)
    assert created["secret"].startswith("whsec_")
    assert created["is_active"] is True
    assert created["events"] == ["document.completed", "document.failed"]

    listed = await client.get("/v1/webhooks", headers=auth_headers)
    assert created["secret"] not in listed.text


async def test_the_secret_is_never_stored(
    client: httpx.AsyncClient, auth_headers: dict[str, str], public_dns: str
) -> None:
    """It is derived from the master secret, so there is nothing at rest to leak."""
    created = await register(client, auth_headers, public_dns)
    async with get_session_factory()() as session:
        row = (await session.execute(select(Webhook))).scalar_one()
    stored = json.dumps(
        {c.name: str(getattr(row, c.name)) for c in row.__table__.columns}
    )
    assert created["secret"] not in stored
    assert "whsec_" not in stored

    # And it can be recomputed exactly.
    assert created["secret"] == derive_secret(
        webhook_id=row.id,
        version=row.secret_version,
        master_secret=get_settings().webhook_secret or "",
    )


async def test_rotation_changes_the_secret_and_keeps_the_endpoint(
    client: httpx.AsyncClient, auth_headers: dict[str, str], public_dns: str
) -> None:
    created = await register(client, auth_headers, public_dns)
    rotated = await client.post(
        f"/v1/webhooks/{created['id']}/rotate", headers=auth_headers
    )
    assert rotated.status_code == 200
    replacement = rotated.json()["data"]
    assert replacement["id"] == created["id"]
    assert replacement["secret"] != created["secret"]


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "https://127.0.0.1/hook",
        "https://[::1]/hook",
        "https://10.0.0.5/hook",
        "https://192.168.1.10/hook",
        "ftp://example.com/hook",
        "https://user:password@example.com/hook",
        "https://example.com:6379/hook",
        "not-a-url",
    ],
)
async def test_unsafe_destinations_are_refused(
    client: httpx.AsyncClient, auth_headers: dict[str, str], url: str
) -> None:
    """A customer-supplied URL is an SSRF vector, not just a string."""
    response = await client.post(
        "/v1/webhooks",
        json={"url": url, "events": ["document.completed"]},
        headers=auth_headers,
    )
    assert response.status_code == 400, url
    assert response.json()["error"]["code"] == "invalid_webhook_url"

    async with get_session_factory()() as session:
        assert (await session.execute(select(Webhook))).scalars().all() == []


async def test_plain_http_is_refused(
    client: httpx.AsyncClient, auth_headers: dict[str, str], public_dns: str
) -> None:
    response = await client.post(
        "/v1/webhooks",
        json={"url": public_dns.replace("https://", "http://"), "events": ["document.completed"]},
        headers=auth_headers,
    )
    assert response.status_code == 400


async def test_webhook_management_requires_a_session_not_an_api_key(
    client: httpx.AsyncClient, tenant: Tenant, public_dns: str
) -> None:
    """A leaked API key must not be able to redirect a customer's events."""
    response = await client.post(
        "/v1/webhooks",
        json={"url": public_dns, "events": ["document.completed"]},
        headers=tenant.headers,
    )
    assert response.status_code == 401


async def test_another_tenant_cannot_see_or_delete_your_webhooks(
    client: httpx.AsyncClient, auth_headers: dict[str, str], public_dns: str
) -> None:
    from tests.conftest import create_tenant

    created = await register(client, auth_headers, public_dns)
    intruder = await create_tenant("Intruder Ltd")
    login = await client.post(
        "/v1/auth/login", json={"email": intruder.email, "password": intruder.password}
    )
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    assert (await client.get("/v1/webhooks", headers=headers)).json()["data"] == []
    assert (
        await client.delete(f"/v1/webhooks/{created['id']}", headers=headers)
    ).status_code == 404


# --- signing -----------------------------------------------------------


def test_a_signature_verifies_and_resists_tampering_and_replay() -> None:
    secret = "whsec_example"
    body = b'{"event":"document.completed","job_id":"job_1"}'
    header = sign_request(body, secret, timestamp=1_700_000_000)

    assert verify_request(body, secret, header, now=1_700_000_030)
    assert not verify_request(b'{"event":"x"}', secret, header, now=1_700_000_030)
    assert not verify_request(body, "whsec_other", header, now=1_700_000_030)
    # Outside the tolerance window: a captured delivery cannot be replayed.
    assert not verify_request(body, secret, header, now=1_700_009_999)


@pytest.mark.parametrize("header", ["", "garbage", "t=abc,v1=xx", "v1=deadbeef", "t=1"])
def test_malformed_signature_headers_are_rejected(header: str) -> None:
    assert not verify_request(b"{}", "whsec_example", header)


def test_backoff_is_exponential_and_capped() -> None:
    delays = [backoff_seconds(a) for a in range(1, 12)]
    assert delays[:4] == [30, 60, 120, 240]
    assert delays == sorted(delays)
    assert max(delays) == 6 * 60 * 60


# --- delivery ----------------------------------------------------------


async def test_a_completed_job_delivers_a_signed_event(
    client: httpx.AsyncClient,
    tenant: Tenant,
    auth_headers: dict[str, str],
    public_dns: str,
    use_provider,
    stub_provider: StubProvider,
    webhook_transport,
) -> None:
    created = await register(client, auth_headers, public_dns)
    use_provider(stub_provider)
    body = await submit(client, tenant)
    await process_available_jobs(get_settings(), limit=5)

    async with get_session_factory()() as session:
        tally = await deliver_due(session, client=webhook_transport)

    assert tally["delivered"] >= 1
    sent = [r for r in webhook_transport.received if "document.completed" in str(r["body"])]
    assert sent, webhook_transport.received

    request = sent[0]
    payload = json.loads(request["body"])
    assert payload["event"] == "document.completed"
    assert payload["job_id"] == body["job_id"]
    assert payload["document_id"] == body["document_id"]
    assert payload["status"] == "completed"
    assert payload["extraction_id"].startswith("ext_")
    assert payload["validation"]["overall"] == "passed"

    signature = request["headers"][SIGNATURE_HEADER.lower()]
    assert verify_request(request["body"], created["secret"], signature)


async def test_a_failed_job_delivers_a_failure_event(
    client: httpx.AsyncClient,
    tenant: Tenant,
    auth_headers: dict[str, str],
    public_dns: str,
    use_provider,
    stub_provider: StubProvider,
    webhook_transport,
) -> None:
    from app.core.errors import ExtractionFailedError

    await register(client, auth_headers, public_dns)
    use_provider(stub_provider)
    await submit(client, tenant)
    use_provider(StubProvider(raises=ExtractionFailedError()))
    await process_available_jobs(get_settings(), limit=5)

    async with get_session_factory()() as session:
        await deliver_due(session, client=webhook_transport)

    events = [json.loads(r["body"])["event"] for r in webhook_transport.received]
    assert "document.failed" in events
    failure = next(
        json.loads(r["body"])
        for r in webhook_transport.received
        if json.loads(r["body"])["event"] == "document.failed"
    )
    assert failure["error"]["code"] == "extraction_failed"


async def test_only_subscribed_events_are_delivered(
    client: httpx.AsyncClient,
    tenant: Tenant,
    auth_headers: dict[str, str],
    public_dns: str,
    use_provider,
    stub_provider: StubProvider,
    webhook_transport,
) -> None:
    await client.post(
        "/v1/webhooks",
        json={"url": public_dns, "events": ["document.processing"]},
        headers=auth_headers,
    )
    use_provider(stub_provider)
    await submit(client, tenant)
    await process_available_jobs(get_settings(), limit=5)

    async with get_session_factory()() as session:
        await deliver_due(session, client=webhook_transport)

    events = {json.loads(r["body"])["event"] for r in webhook_transport.received}
    assert events == {"document.processing"}


async def test_a_5xx_is_retried_with_backoff(
    client: httpx.AsyncClient,
    tenant: Tenant,
    auth_headers: dict[str, str],
    public_dns: str,
    use_provider,
    stub_provider: StubProvider,
    webhook_transport,
) -> None:
    await register(client, auth_headers, public_dns)
    use_provider(stub_provider)
    await submit(client, tenant)
    await process_available_jobs(get_settings(), limit=5)

    webhook_transport.script.extend([503])
    async with get_session_factory()() as session:
        tally = await deliver_due(session, client=webhook_transport)
    assert tally["retrying"] == 1

    async with get_session_factory()() as session:
        delivery = (await session.execute(select(WebhookDelivery))).scalar_one()
    assert delivery.status == "pending"
    assert delivery.attempts == 1
    assert delivery.response_status == 503
    assert delivery.next_attempt_at > delivery.created_at


async def test_a_4xx_is_not_retried(
    client: httpx.AsyncClient,
    tenant: Tenant,
    auth_headers: dict[str, str],
    public_dns: str,
    use_provider,
    stub_provider: StubProvider,
    webhook_transport,
) -> None:
    """410 Gone means the receiver understood and refused. Retrying is rude."""
    await register(client, auth_headers, public_dns)
    use_provider(stub_provider)
    await submit(client, tenant)
    await process_available_jobs(get_settings(), limit=5)

    webhook_transport.script.extend([410])
    async with get_session_factory()() as session:
        tally = await deliver_due(session, client=webhook_transport)
    assert tally["failed"] == 1

    async with get_session_factory()() as session:
        delivery = (await session.execute(select(WebhookDelivery))).scalar_one()
    assert delivery.status == "failed"
    assert delivery.attempts == 1


async def test_delivery_stops_after_max_attempts(
    client: httpx.AsyncClient,
    tenant: Tenant,
    auth_headers: dict[str, str],
    public_dns: str,
    use_provider,
    stub_provider: StubProvider,
    webhook_transport,
) -> None:
    from app.db.base import utcnow

    await register(client, auth_headers, public_dns)
    use_provider(stub_provider)
    await submit(client, tenant)
    await process_available_jobs(get_settings(), limit=5)

    for _ in range(5):
        webhook_transport.script.extend([500])
        async with get_session_factory()() as session:
            delivery = (await session.execute(select(WebhookDelivery))).scalar_one()
            delivery.next_attempt_at = utcnow()
            await session.commit()
            await deliver_due(session, client=webhook_transport)

    async with get_session_factory()() as session:
        delivery = (await session.execute(select(WebhookDelivery))).scalar_one()
    assert delivery.status == "failed"
    assert delivery.attempts == delivery.max_attempts == 3


async def test_a_connection_failure_is_retried(
    client: httpx.AsyncClient,
    tenant: Tenant,
    auth_headers: dict[str, str],
    public_dns: str,
    use_provider,
    stub_provider: StubProvider,
    webhook_transport,
) -> None:
    await register(client, auth_headers, public_dns)
    use_provider(stub_provider)
    await submit(client, tenant)
    await process_available_jobs(get_settings(), limit=5)

    webhook_transport.script.extend([0])  # raises ConnectError
    async with get_session_factory()() as session:
        tally = await deliver_due(session, client=webhook_transport)
    assert tally["retrying"] == 1


async def test_the_receivers_response_body_is_not_stored(
    client: httpx.AsyncClient,
    tenant: Tenant,
    auth_headers: dict[str, str],
    public_dns: str,
    use_provider,
    stub_provider: StubProvider,
    webhook_transport,
) -> None:
    await register(client, auth_headers, public_dns)
    use_provider(stub_provider)
    await submit(client, tenant)
    await process_available_jobs(get_settings(), limit=5)

    webhook_transport.script.extend([500])
    async with get_session_factory()() as session:
        await deliver_due(session, client=webhook_transport)
        delivery = (await session.execute(select(WebhookDelivery))).scalar_one()
    assert delivery.error == "endpoint returned 500"


async def test_deliveries_are_listed_for_the_dashboard(
    client: httpx.AsyncClient,
    tenant: Tenant,
    auth_headers: dict[str, str],
    public_dns: str,
    use_provider,
    stub_provider: StubProvider,
    webhook_transport,
) -> None:
    await register(client, auth_headers, public_dns)
    use_provider(stub_provider)
    await submit(client, tenant)
    await process_available_jobs(get_settings(), limit=5)
    async with get_session_factory()() as session:
        await deliver_due(session, client=webhook_transport)

    listed = await client.get("/v1/webhooks/deliveries", headers=auth_headers)
    assert listed.status_code == 200
    rows = listed.json()["data"]
    assert len(rows) == 1
    assert rows[0]["status"] == "delivered"
    assert rows[0]["event"] == "document.completed"


async def test_a_disabled_endpoint_receives_nothing(
    client: httpx.AsyncClient,
    tenant: Tenant,
    auth_headers: dict[str, str],
    public_dns: str,
    use_provider,
    stub_provider: StubProvider,
    webhook_transport,
) -> None:
    created = await register(client, auth_headers, public_dns)
    await client.post(f"/v1/webhooks/{created['id']}/disable", headers=auth_headers)

    use_provider(stub_provider)
    await submit(client, tenant)
    await process_available_jobs(get_settings(), limit=5)
    async with get_session_factory()() as session:
        await deliver_due(session, client=webhook_transport)

    assert webhook_transport.received == []


async def test_a_destination_that_turns_unsafe_is_not_delivered_to(
    client: httpx.AsyncClient,
    tenant: Tenant,
    auth_headers: dict[str, str],
    public_dns: str,
    use_provider,
    stub_provider: StubProvider,
    webhook_transport,
    monkeypatch,
) -> None:
    """DNS rebinding: safe at registration, private by delivery time."""
    await register(client, auth_headers, public_dns)
    use_provider(stub_provider)
    await submit(client, tenant)
    await process_available_jobs(get_settings(), limit=5)

    import app.services.webhook_security as security

    monkeypatch.setattr(security, "_resolve", lambda host, port: ["127.0.0.1"])
    async with get_session_factory()() as session:
        await deliver_due(session, client=webhook_transport)

    assert webhook_transport.received == []
    async with get_session_factory()() as session:
        delivery = (await session.execute(select(WebhookDelivery))).scalar_one()
    assert delivery.status == "failed"
    assert "unsafe destination" in (delivery.error or "")


async def test_the_destination_guard_runs_for_real_addresses() -> None:
    settings = Settings(webhook_secret="x", app_env="test")
    with pytest.raises(UnsafeWebhookURLError):
        await assert_destination_is_safe("https://127.0.0.1/hook", settings)


async def test_the_metadata_address_is_blocked_even_in_permissive_mode() -> None:
    """The development flag is not a reason to allow 169.254.169.254.

    Letting a webhook reach the cloud metadata service is the whole prize in
    a webhook SSRF, so that range stays closed whatever the configuration.
    """
    permissive = Settings(
        webhook_secret="x",
        app_env="development",
        webhook_allow_private_urls=True,
        webhook_require_https=False,
    )
    for url in [
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.170.2/v2/credentials",
        "http://[fe80::1]/hook",
    ]:
        with pytest.raises(UnsafeWebhookURLError):
            await assert_destination_is_safe(url, permissive)


async def test_localhost_still_works_in_permissive_mode() -> None:
    """The flag exists so a developer can use a receiver on their own machine."""
    permissive = Settings(
        webhook_secret="x",
        app_env="development",
        webhook_allow_private_urls=True,
        webhook_require_https=False,
    )
    destination = await assert_destination_is_safe(
        "http://127.0.0.1:8090/hook", permissive
    )
    assert destination.host == "127.0.0.1"
    assert destination.port == 8090
