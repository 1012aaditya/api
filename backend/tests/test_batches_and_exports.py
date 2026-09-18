"""Bulk upload and spreadsheet export (§25)."""

from __future__ import annotations

import csv
import io

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.models import Document, DocumentBatch, ExtractionJob, Organization, UsageEvent
from app.workers.worker import process_available_jobs
from tests.conftest import StubProvider, Tenant
from tests.fixtures.invoices import InvoiceSpec, build_invoice_pdf, build_png

BATCH = "/v1/batches"
GOOD_PDF = ("a.pdf", build_invoice_pdf(), "application/pdf")
BAD_GIF = ("bad.gif", b"GIF89a" + b"\x00" * 60, "image/gif")


def files(*entries: tuple[str, bytes, str]) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", entry) for entry in entries]


async def upload_batch(
    client: httpx.AsyncClient, tenant: Tenant, *entries, name: str | None = None
) -> dict:
    data = {"name": name} if name else None
    response = await client.post(
        BATCH, files=files(*entries), data=data, headers=tenant.headers
    )
    assert response.status_code == 202, response.text
    return response.json()


def read_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))


# --- bulk upload -------------------------------------------------------


async def test_a_batch_queues_every_good_file(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    body = await upload_batch(
        client, tenant,
        ("a.pdf", build_invoice_pdf(), "application/pdf"),
        ("b.pdf", build_invoice_pdf(InvoiceSpec(extra_pages=1)), "application/pdf"),
        ("c.png", build_png(), "image/png"),
        name="September purchases",
    )

    assert body["batch_id"].startswith("bat_")
    assert body["accepted"] == 3
    assert body["rejected"] == []
    assert len(body["job_ids"]) == 3

    async with get_session_factory()() as session:
        jobs = list((await session.execute(select(ExtractionJob))).scalars())
        documents = list((await session.execute(select(Document))).scalars())
    assert len(jobs) == 3
    assert {job.batch_id for job in jobs} == {body["batch_id"]}
    assert {doc.batch_id for doc in documents} == {body["batch_id"]}


async def test_one_bad_file_does_not_fail_the_batch(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    """197 good invoices should not be lost to 3 corrupt ones."""
    use_provider(stub_provider)
    body = await upload_batch(
        client, tenant,
        GOOD_PDF,
        BAD_GIF,
        ("broken.pdf", b"%PDF-1.4 truncated", "application/pdf"),
        ("d.pdf", build_invoice_pdf(), "application/pdf"),
    )

    assert body["accepted"] == 2
    rejected = {item["filename"]: item["code"] for item in body["rejected"]}
    assert rejected == {"bad.gif": "unsupported_file_type", "broken.pdf": "invalid_file"}
    # Every rejection carries a reason, not just a count.
    assert all(item["message"] for item in body["rejected"])


async def test_batch_progress_moves_as_the_worker_runs(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    body = await upload_batch(
        client, tenant, GOOD_PDF, ("b.pdf", build_invoice_pdf(), "application/pdf")
    )
    batch_id = body["batch_id"]

    before = (await client.get(f"{BATCH}/{batch_id}", headers=tenant.headers)).json()["data"]
    assert before["total"] == 2
    assert before["queued"] == 2
    assert before["done"] is False

    await process_available_jobs(get_settings(), limit=10)

    after = (await client.get(f"{BATCH}/{batch_id}", headers=tenant.headers)).json()["data"]
    assert after["completed"] == 2
    assert after["queued"] == 0
    assert after["done"] is True
    assert after["name"] is None or isinstance(after["name"], str)


async def test_a_batch_is_listed_for_its_organization_only(
    client: httpx.AsyncClient,
    tenant: Tenant,
    other_tenant: Tenant,
    use_provider,
    stub_provider: StubProvider,
) -> None:
    use_provider(stub_provider)
    body = await upload_batch(client, tenant, GOOD_PDF)

    mine = (await client.get(BATCH, headers=tenant.headers)).json()["data"]
    theirs = (await client.get(BATCH, headers=other_tenant.headers)).json()["data"]
    assert [b["id"] for b in mine] == [body["batch_id"]]
    assert theirs == []

    intruder = await client.get(f"{BATCH}/{body['batch_id']}", headers=other_tenant.headers)
    assert intruder.status_code == 404


async def test_the_batch_stops_at_the_monthly_quota(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    """Queue what fits, refuse the rest, and say which files those were."""
    async with get_session_factory()() as session:
        organization = (
            await session.execute(
                select(Organization).where(Organization.id == tenant.organization_id)
            )
        ).scalar_one()
        organization.monthly_document_quota = 2
        await session.commit()

    use_provider(stub_provider)
    body = await upload_batch(
        client, tenant,
        ("a.pdf", build_invoice_pdf(), "application/pdf"),
        ("b.pdf", build_invoice_pdf(), "application/pdf"),
        ("c.pdf", build_invoice_pdf(), "application/pdf"),
    )
    assert body["accepted"] == 2
    assert [item["code"] for item in body["rejected"]] == ["quota_exceeded"]
    assert body["rejected"][0]["filename"] == "c.pdf"


async def test_too_many_files_is_refused_before_anything_is_stored(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider,
    app,
) -> None:
    from app.core.config import Settings
    from app.core.config import get_settings as real_get_settings

    small = Settings(**{**real_get_settings().model_dump(), "max_batch_files": 2})
    app.dependency_overrides[real_get_settings] = lambda: small
    try:
        use_provider(stub_provider)
        response = await client.post(
            BATCH,
            files=files(GOOD_PDF, ("b.pdf", build_invoice_pdf(), "application/pdf"),
                        ("c.pdf", build_invoice_pdf(), "application/pdf")),
            headers=tenant.headers,
        )
    finally:
        app.dependency_overrides.pop(real_get_settings, None)

    assert response.status_code == 400
    assert response.json()["error"]["details"]["max_files"] == 2
    async with get_session_factory()() as session:
        assert (await session.execute(select(DocumentBatch))).scalars().all() == []


async def test_batch_submissions_are_not_billable_until_processed(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    await upload_batch(
        client, tenant, GOOD_PDF, ("b.pdf", build_invoice_pdf(), "application/pdf")
    )
    async with get_session_factory()() as session:
        events = list((await session.execute(select(UsageEvent))).scalars())
    assert events and all(not event.billable for event in events)


# --- CSV export --------------------------------------------------------


async def test_the_invoice_csv_carries_what_an_accountant_needs(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    await client.post(
        "/v1/invoices/extract",
        files={"file": ("i.pdf", build_invoice_pdf(), "application/pdf")},
        headers=tenant.headers,
    )

    response = await client.get("/v1/exports/invoices.csv", headers=tenant.headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    # A BOM, so Excel on Windows opens a rupee sign correctly.
    assert response.text.startswith("﻿")

    rows = read_csv(response.text)
    assert len(rows) == 1
    row = rows[0]
    assert row["invoice_number"] == "INV-29381"
    assert row["invoice_date"] == "2026-09-18"
    assert row["supplier_gstin"] == "29AABCU9603R1ZJ"
    assert row["total"] == "118000"
    assert row["cgst"] == "9000"
    assert row["validation"] == "passed"
    assert row["gstin_format_valid"] == "yes"
    assert row["line_item_count"] == "2"
    assert row["document_id"].startswith("doc_")


async def test_a_missing_field_is_an_empty_cell_not_the_word_none(
    client: httpx.AsyncClient, tenant: Tenant, use_provider
) -> None:
    """A spreadsheet formula over "None" silently produces nonsense."""
    from tests.fixtures.invoices import sparse_provider_output

    use_provider(StubProvider(sparse_provider_output()))
    await client.post(
        "/v1/invoices/extract",
        files={"file": ("scan.png", build_png(), "image/png")},
        headers=tenant.headers,
    )
    rows = read_csv(
        (await client.get("/v1/exports/invoices.csv", headers=tenant.headers)).text
    )
    assert rows[0]["buyer_gstin"] == ""
    assert rows[0]["due_date"] == ""
    assert "None" not in rows[0].values()


async def test_the_line_item_csv_has_one_row_per_item(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    await client.post(
        "/v1/invoices/extract",
        files={"file": ("i.pdf", build_invoice_pdf(), "application/pdf")},
        headers=tenant.headers,
    )

    rows = read_csv(
        (await client.get("/v1/exports/line-items.csv", headers=tenant.headers)).text
    )
    assert len(rows) == 2
    assert rows[0]["line_no"] == "1"
    assert rows[0]["hsn_sac"] == "998314"
    assert rows[0]["taxable_value"] == "80000"
    assert rows[1]["line_no"] == "2"
    # Each row carries the invoice it belongs to, for reconciliation.
    assert all(row["invoice_number"] == "INV-29381" for row in rows)


async def test_an_export_only_contains_your_own_invoices(
    client: httpx.AsyncClient,
    tenant: Tenant,
    other_tenant: Tenant,
    use_provider,
    stub_provider: StubProvider,
) -> None:
    use_provider(stub_provider)
    for owner in (tenant, other_tenant, other_tenant):
        await client.post(
            "/v1/invoices/extract",
            files={"file": ("i.pdf", build_invoice_pdf(), "application/pdf")},
            headers=owner.headers,
        )

    mine = read_csv(
        (await client.get("/v1/exports/invoices.csv", headers=tenant.headers)).text
    )
    theirs = read_csv(
        (await client.get("/v1/exports/invoices.csv", headers=other_tenant.headers)).text
    )
    assert len(mine) == 1
    assert len(theirs) == 2
    assert not {row["document_id"] for row in mine} & {row["document_id"] for row in theirs}


async def test_an_export_can_be_scoped_to_one_batch(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    body = await upload_batch(client, tenant, GOOD_PDF)
    await client.post(
        "/v1/invoices/extract",
        files={"file": ("loose.pdf", build_invoice_pdf(), "application/pdf")},
        headers=tenant.headers,
    )
    await process_available_jobs(get_settings(), limit=10)

    everything = read_csv(
        (await client.get("/v1/exports/invoices.csv", headers=tenant.headers)).text
    )
    just_batch = read_csv(
        (
            await client.get(
                f"/v1/exports/invoices.csv?batch_id={body['batch_id']}",
                headers=tenant.headers,
            )
        ).text
    )
    assert len(everything) == 2
    assert len(just_batch) == 1


async def test_an_unknown_batch_filter_is_a_404(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    response = await client.get(
        "/v1/exports/invoices.csv?batch_id=bat_nope", headers=tenant.headers
    )
    assert response.status_code == 404


async def test_an_empty_export_is_a_header_row_not_an_error(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    response = await client.get("/v1/exports/invoices.csv", headers=tenant.headers)
    assert response.status_code == 200
    assert read_csv(response.text) == []
    assert "invoice_number" in response.text


async def test_a_backwards_date_window_is_refused(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    response = await client.get(
        "/v1/exports/invoices.csv?from=2026-09-30&to=2026-09-01", headers=tenant.headers
    )
    assert response.status_code == 400
