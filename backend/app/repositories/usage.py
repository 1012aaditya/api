from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UsageEvent


def month_start(now: dt.datetime) -> dt.datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class UsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        organization_id: str,
        endpoint: str,
        status_code: int,
        success: bool,
        billable: bool,
        request_id: str | None = None,
        api_key_id: str | None = None,
        document_id: str | None = None,
        event_type: str = "api_request",
        pages: int = 0,
        provider: str | None = None,
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        estimated_cost_usd: Decimal | None = None,
        duration_ms: int | None = None,
        error_code: str | None = None,
    ) -> UsageEvent:
        event = UsageEvent(
            organization_id=organization_id,
            endpoint=endpoint,
            status_code=status_code,
            success=success,
            billable=billable,
            request_id=request_id,
            api_key_id=api_key_id,
            document_id=document_id,
            event_type=event_type,
            pages=pages,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated_cost_usd,
            duration_ms=duration_ms,
            error_code=error_code,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def billable_count_this_month(
        self, organization_id: str, *, now: dt.datetime
    ) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(UsageEvent)
            .where(
                UsageEvent.organization_id == organization_id,
                UsageEvent.billable.is_(True),
                UsageEvent.created_at >= month_start(now),
            )
        )
        return int(result.scalar_one())

    async def summary(
        self, organization_id: str, *, since: dt.datetime
    ) -> dict[str, object]:
        result = await self.session.execute(
            select(
                func.count().label("requests"),
                func.sum(func.cast(UsageEvent.success, Integer)).label("successful"),
                func.sum(UsageEvent.pages).label("pages"),
                func.avg(UsageEvent.duration_ms).label("avg_duration_ms"),
                func.sum(UsageEvent.estimated_cost_usd).label("estimated_cost_usd"),
            ).where(
                UsageEvent.organization_id == organization_id,
                UsageEvent.created_at >= since,
            )
        )
        row = result.one()
        requests = int(row.requests or 0)
        successful = int(row.successful or 0)
        return {
            "requests": requests,
            "successful_requests": successful,
            "failed_requests": requests - successful,
            "pages": int(row.pages or 0),
            "average_duration_ms": round(float(row.avg_duration_ms), 2)
            if row.avg_duration_ms is not None
            else None,
            "estimated_cost_usd": Decimal(row.estimated_cost_usd)
            if row.estimated_cost_usd is not None
            else Decimal("0"),
        }
