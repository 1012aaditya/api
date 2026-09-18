"""Usage reporting for the dashboard (§21, §25).

Everything here is derived from ``usage_events``, which is written for every
authenticated request. The numbers are what actually happened, not a sample.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, authenticate_any, get_request_id
from app.db.base import utcnow
from app.db.session import get_db
from app.models import UsageEvent
from app.repositories.usage import UsageRepository, month_start
from app.schemas.common import SuccessResponse

router = APIRouter(prefix="/usage", tags=["usage"])


class UsageTotals(BaseModel):
    requests: int
    successful_requests: int
    failed_requests: int
    pages: int
    average_duration_ms: float | None
    estimated_cost_usd: float


class QuotaStatus(BaseModel):
    documents_used: int
    monthly_quota: int
    remaining: int
    percent_used: float
    period_start: dt.datetime
    rate_limit_per_minute: int


class DailyPoint(BaseModel):
    day: str
    requests: int
    successful: int
    failed: int
    documents: int


class UsageSummary(BaseModel):
    window_days: int
    totals: UsageTotals
    quota: QuotaStatus
    daily: list[DailyPoint] = Field(default_factory=list)
    success_rate: float | None = Field(
        default=None, description="Successful requests over total, 0-1. Null if no requests."
    )


class UsageEventSummary(BaseModel):
    id: str
    request_id: str | None
    endpoint: str
    event_type: str
    status_code: int
    success: bool
    billable: bool
    pages: int
    model: str | None
    duration_ms: int | None
    estimated_cost_usd: float | None
    error_code: str | None
    document_id: str | None
    created_at: dt.datetime


def _event(event: UsageEvent) -> UsageEventSummary:
    return UsageEventSummary(
        id=event.id,
        request_id=event.request_id,
        endpoint=event.endpoint,
        event_type=event.event_type,
        status_code=event.status_code,
        success=event.success,
        billable=event.billable,
        pages=event.pages,
        model=event.model,
        duration_ms=event.duration_ms,
        estimated_cost_usd=float(event.estimated_cost_usd)
        if event.estimated_cost_usd is not None
        else None,
        error_code=event.error_code,
        document_id=event.document_id,
        created_at=event.created_at,
    )


@router.get(
    "",
    response_model=SuccessResponse[UsageSummary],
    summary="Usage totals, quota status and a daily series",
)
async def get_usage(
    days: int = Query(default=30, ge=1, le=365),
    auth: AuthContext = Depends(authenticate_any),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[UsageSummary]:
    now = utcnow()
    since = now - dt.timedelta(days=days)
    repo = UsageRepository(db)

    totals = await repo.summary(auth.organization_id, since=since)
    used = await repo.billable_count_this_month(auth.organization_id, now=now)
    daily = await repo.daily_series(auth.organization_id, since=since, until=now)

    quota = auth.monthly_document_quota
    requests = int(totals["requests"])
    successful = int(totals["successful_requests"])

    return SuccessResponse(
        request_id=request_id,
        data=UsageSummary(
            window_days=days,
            totals=UsageTotals(
                requests=requests,
                successful_requests=successful,
                failed_requests=int(totals["failed_requests"]),
                pages=int(totals["pages"]),
                average_duration_ms=totals["average_duration_ms"],
                estimated_cost_usd=float(totals["estimated_cost_usd"]),
            ),
            quota=QuotaStatus(
                documents_used=used,
                monthly_quota=quota,
                remaining=max(0, quota - used),
                percent_used=round(used / quota * 100, 2) if quota else 0.0,
                period_start=month_start(now),
                rate_limit_per_minute=auth.rate_limit_per_minute,
            ),
            daily=[DailyPoint(**point) for point in daily],
            # None, not 0 or 100: a success rate over no requests is not a
            # number worth showing.
            success_rate=round(successful / requests, 4) if requests else None,
        ),
    )


@router.get(
    "/events",
    response_model=SuccessResponse[list[UsageEventSummary]],
    summary="Recent requests, newest first",
)
async def list_usage_events(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(authenticate_any),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[UsageEventSummary]]:
    events = await UsageRepository(db).recent_events(
        auth.organization_id, limit=limit, offset=offset
    )
    return SuccessResponse(request_id=request_id, data=[_event(e) for e in events])
