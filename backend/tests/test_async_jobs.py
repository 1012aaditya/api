"""Asynchronous document processing (§6)."""

from __future__ import annotations

import datetime as dt

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.errors import ExtractionFailedError, ProviderUnavailableError
from app.db.base import utcnow
from app.db.session import get_session_factory
from app.models import Document, Extraction, ExtractionJob, JobStatus, UsageEvent
from app.repositories.jobs import JobRepository
from app.workers.worker import process_available_jobs, release_stale_jobs, run_once
from tests.conftest import StubProvider, Tenant
from tests.fixtures.invoices import InvoiceSpec, build_invoice_pdf

SUBMIT = "/v1/documents"


async def submit(client: httpx.AsyncClient, tenant: Tenant, *, pages: int = 0) -> dict:
    content = build_invoice_pdf(InvoiceSpec(extra_pages=pages))
    response = await client.post(
        SUBMIT,
        files={"file": ("invoice.pdf", content, "application/pdf")},
        headers=tenant.headers,
    )
    assert response.status_code == 202, response.text
    return response.json()


# --- submission --------------------------------------------------------


async def test_submission_returns_a_job_id_immediately(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    body = await submit(client, tenant)

    assert body["success"] is True
    assert body["job_id"].startswith("job_")
    assert body["document_id"].startswith("doc_")
    assert body["status"] == "queued"
    assert body["request_id"].startswith("req_")
    # Nothing has been extracted yet — the provider has not been called.
    assert stub_provider.calls == []


async def test_the_document_is_stored_so_the_worker_can_read_it(
    client: httpx.AsyncClient,
    tenant: Tenant,
    use_provider,
    stub_provider: StubProvider,
    object_store,
) -> None:
    use_provider(stub_provider)
    await submit(client, tenant)
    async with get_session_factory()() as session:
        document = (await session.execute(select(Document))).scalar_one()
    assert document.storage_key is not None
    assert document.storage_key in object_store.objects


async def test_submission_is_not_billable_until_the_work_happens(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    await submit(client, tenant)
    async with get_session_factory()() as session:
        event = (await session.execute(select(UsageEvent))).scalar_one()
    assert event.status_code == 202
    assert event.billable is False


async def test_a_bad_file_is_refused_before_a_job_exists(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    response = await client.post(
        SUBMIT,
        files={"file": ("x.gif", b"GIF89a" + b"\x00" * 50, "image/gif")},
        headers=tenant.headers,
    )
    assert response.status_code == 415
    async with get_session_factory()() as session:
        assert (await session.execute(select(ExtractionJob))).scalars().all() == []


async def test_an_unconfigured_provider_refuses_the_submission(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    """Queueing work that can never run would just defer the same 503."""
    response = await client.post(
        SUBMIT,
        files={"file": ("i.pdf", build_invoice_pdf(), "application/pdf")},
        headers=tenant.headers,
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "extraction_provider_unavailable"
    async with get_session_factory()() as session:
        assert (await session.execute(select(ExtractionJob))).scalars().all() == []


# --- status ------------------------------------------------------------


async def test_job_status_moves_from_queued_to_completed(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    job_id = (await submit(client, tenant))["job_id"]

    queued = await client.get(f"/v1/jobs/{job_id}", headers=tenant.headers)
    assert queued.json()["data"]["status"] == "queued"
    assert queued.json()["data"]["extraction_id"] is None

    await process_available_jobs(get_settings(), limit=5)

    done = (await client.get(f"/v1/jobs/{job_id}", headers=tenant.headers)).json()["data"]
    assert done["status"] == "completed"
    assert done["extraction_id"].startswith("ext_")
    assert done["attempts"] == 1
    assert done["error"] is None


async def test_the_result_is_fetchable_once_the_job_completes(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    body = await submit(client, tenant, pages=2)
    await process_available_jobs(get_settings(), limit=5)

    stored = await client.get(
        f"/v1/documents/{body['document_id']}/extraction", headers=tenant.headers
    )
    assert stored.status_code == 200
    data = stored.json()["data"]
    assert data["status"] == "succeeded"
    assert data["data"]["invoice_number"] == "INV-29381"
    assert data["validation"]["overall"] == "passed"


async def test_the_worker_records_billable_usage(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    await submit(client, tenant)
    await process_available_jobs(get_settings(), limit=5)

    async with get_session_factory()() as session:
        events = list((await session.execute(select(UsageEvent))).scalars())
    billable = [e for e in events if e.billable]
    assert len(billable) == 1
    assert billable[0].event_type == "async_job"
    assert billable[0].success is True


async def test_an_unknown_job_is_a_404(client: httpx.AsyncClient, tenant: Tenant) -> None:
    response = await client.get("/v1/jobs/job_nope", headers=tenant.headers)
    assert response.status_code == 404


async def test_another_tenants_job_is_reported_as_absent(
    client: httpx.AsyncClient,
    tenant: Tenant,
    other_tenant: Tenant,
    use_provider,
    stub_provider: StubProvider,
) -> None:
    use_provider(stub_provider)
    job_id = (await submit(client, tenant))["job_id"]
    response = await client.get(f"/v1/jobs/{job_id}", headers=other_tenant.headers)
    assert response.status_code == 404

    listed = (await client.get("/v1/jobs", headers=other_tenant.headers)).json()["data"]
    assert listed == []


# --- failure and retry -------------------------------------------------


async def test_a_provider_outage_is_retried(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    job_id = (await submit(client, tenant))["job_id"]

    use_provider(StubProvider(raises=ProviderUnavailableError()))
    await process_available_jobs(get_settings(), limit=5)

    status = (await client.get(f"/v1/jobs/{job_id}", headers=tenant.headers)).json()["data"]
    # Back in the queue, not failed — the document is fine, the provider was not.
    assert status["status"] == "queued"
    assert status["attempts"] == 1
    assert status["error"]["code"] == "extraction_provider_unavailable"

    async with get_session_factory()() as session:
        job = (await session.execute(select(ExtractionJob))).scalar_one()
    assert job.available_at > utcnow()  # backed off


async def test_an_unparseable_document_is_not_retried(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    """Same bytes, same prompt, same answer — a retry would just cost money."""
    use_provider(stub_provider)
    job_id = (await submit(client, tenant))["job_id"]

    use_provider(StubProvider(raises=ExtractionFailedError("model returned prose")))
    await process_available_jobs(get_settings(), limit=5)

    status = (await client.get(f"/v1/jobs/{job_id}", headers=tenant.headers)).json()["data"]
    assert status["status"] == "failed"
    assert status["attempts"] == 1
    assert status["error"]["code"] == "extraction_failed"


async def test_retries_are_bounded_by_max_attempts(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    job_id = (await submit(client, tenant))["job_id"]
    use_provider(StubProvider(raises=ProviderUnavailableError()))

    for _ in range(5):
        # Make the job due again so the retry delay does not stall the test.
        async with get_session_factory()() as session:
            job = await JobRepository(session).get(tenant.organization_id, job_id)
            if job is not None and job.status == JobStatus.QUEUED:
                job.available_at = utcnow() - dt.timedelta(seconds=1)
                await session.commit()
        await process_available_jobs(get_settings(), limit=5)

    status = (await client.get(f"/v1/jobs/{job_id}", headers=tenant.headers)).json()["data"]
    assert status["status"] == "failed"
    assert status["attempts"] == status["max_attempts"] == 3


async def test_a_failed_job_marks_the_document_and_stores_the_failure(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    await submit(client, tenant)
    use_provider(StubProvider(raises=ExtractionFailedError()))
    await process_available_jobs(get_settings(), limit=5)

    async with get_session_factory()() as session:
        document = (await session.execute(select(Document))).scalar_one()
        extraction = (await session.execute(select(Extraction))).scalar_one()
    assert document.status == "failed"
    assert extraction.status == "failed"
    assert extraction.error_code == "extraction_failed"


async def test_a_deleted_document_fails_its_job_without_retrying(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    body = await submit(client, tenant)
    await client.delete(f"/v1/documents/{body['document_id']}", headers=tenant.headers)

    await process_available_jobs(get_settings(), limit=5)
    status = (
        await client.get(f"/v1/jobs/{body['job_id']}", headers=tenant.headers)
    ).json()["data"]
    assert status["status"] == "failed"
    assert status["error"]["code"] == "document_unavailable"


# --- the worker loop ---------------------------------------------------


async def test_the_worker_claims_each_job_once(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    for _ in range(3):
        await submit(client, tenant)

    assert await process_available_jobs(get_settings(), limit=10) == 3
    # Nothing left to claim.
    assert await process_available_jobs(get_settings(), limit=10) == 0
    assert len(stub_provider.calls) == 3


async def test_a_batch_limit_is_respected(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    for _ in range(4):
        await submit(client, tenant)
    assert await process_available_jobs(get_settings(), limit=2) == 2


async def test_a_job_abandoned_by_a_dead_worker_is_requeued(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    job_id = (await submit(client, tenant))["job_id"]

    async with get_session_factory()() as session:
        job = await JobRepository(session).get(tenant.organization_id, job_id)
        assert job is not None
        job.status = JobStatus.PROCESSING
        job.started_at = utcnow() - dt.timedelta(hours=2)
        await session.commit()

    assert await release_stale_jobs(get_settings()) == 1
    status = (await client.get(f"/v1/jobs/{job_id}", headers=tenant.headers)).json()["data"]
    assert status["status"] == "queued"


async def test_a_fresh_processing_job_is_not_stolen(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    job_id = (await submit(client, tenant))["job_id"]
    async with get_session_factory()() as session:
        job = await JobRepository(session).get(tenant.organization_id, job_id)
        assert job is not None
        job.status = JobStatus.PROCESSING
        job.started_at = utcnow()
        await session.commit()

    assert await release_stale_jobs(get_settings()) == 0


async def test_one_worker_pass_processes_and_reports(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    await submit(client, tenant)
    tally = await run_once(get_settings())
    assert tally["jobs"] == 1
    assert tally["released"] == 0
