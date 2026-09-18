"""Spreadsheet exports (§25).

The fastest way to make extracted data useful to someone who does not write
code. Two files: one row per invoice, and one row per line item for anyone
reconciling HSN-wise.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, enforce_rate_limit, get_request_id
from app.core.errors import InvalidRequestError, NotFoundError
from app.db.session import get_db
from app.repositories.batches import BatchRepository
from app.services.export import stream_invoices_csv, stream_line_items_csv

router = APIRouter(prefix="/exports", tags=["exports"])

MAX_WINDOW_DAYS = 400


def _window(
    since: dt.date | None, until: dt.date | None
) -> tuple[dt.datetime | None, dt.datetime | None]:
    start = (
        dt.datetime.combine(since, dt.time.min, tzinfo=dt.UTC)
        if since
        else None
    )
    end = (
        dt.datetime.combine(until, dt.time.max, tzinfo=dt.UTC) if until else None
    )
    if start and end:
        if end < start:
            raise InvalidRequestError("'until' is before 'from'.")
        if (end - start).days > MAX_WINDOW_DAYS:
            raise InvalidRequestError(
                f"Export windows are limited to {MAX_WINDOW_DAYS} days."
            )
    return start, end


async def _assert_batch_exists(
    db: AsyncSession, organization_id: str, batch_id: str | None
) -> None:
    if batch_id is None:
        return
    if await BatchRepository(db).get(organization_id, batch_id) is None:
        raise NotFoundError("No batch with that id exists in this organization.")


def _filename(prefix: str) -> str:
    return f"{prefix}-{dt.datetime.now(dt.UTC):%Y%m%d}.csv"


@router.get(
    "/invoices.csv",
    summary="Extracted invoices as CSV, one row per document",
    response_class=StreamingResponse,
)
async def export_invoices(
    since: dt.date | None = Query(default=None, alias="from"),
    until: dt.date | None = Query(default=None, alias="to"),
    batch_id: str | None = Query(default=None),
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> StreamingResponse:
    start, end = _window(since, until)
    await _assert_batch_exists(db, auth.organization_id, batch_id)
    return StreamingResponse(
        stream_invoices_csv(
            db, auth.organization_id, since=start, until=end, batch_id=batch_id
        ),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename("invoices")}"',
            "X-Request-Id": request_id,
        },
    )


@router.get(
    "/line-items.csv",
    summary="Extracted line items as CSV, one row per item",
    response_class=StreamingResponse,
)
async def export_line_items(
    since: dt.date | None = Query(default=None, alias="from"),
    until: dt.date | None = Query(default=None, alias="to"),
    batch_id: str | None = Query(default=None),
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> StreamingResponse:
    start, end = _window(since, until)
    await _assert_batch_exists(db, auth.organization_id, batch_id)
    return StreamingResponse(
        stream_line_items_csv(
            db, auth.organization_id, since=start, until=end, batch_id=batch_id
        ),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename("line-items")}"',
            "X-Request-Id": request_id,
        },
    )
