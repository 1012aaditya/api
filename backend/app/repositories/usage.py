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

    def _day_expression(self):
        """A ``YYYY-MM-DD`` day key, computed in the database.

        Deliberately dialect-aware rather than clever: Postgres and SQLite
        disagree about date functions, and getting this wrong silently
        buckets a dashboard's chart into the wrong days.
        """
        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            return func.to_char(func.timezone("UTC", UsageEvent.created_at), "YYYY-MM-DD")
        return func.strftime("%Y-%m-%d", UsageEvent.created_at)

    async def daily_series(
        self, organization_id: str, *, since: dt.datetime, until: dt.datetime | None = None
    ) -> list[dict[str, object]]:
        """Per-day request counts, with every day in the window present.

        Days with no traffic are returned as zeroes rather than omitted. A
        series that skips empty days draws two months apart as neighbours and
        silently misstates the shape of the traffic.
        """
        day = self._day_expression().label("day")
        result = await self.session.execute(
            select(
                day,
                func.count().label("requests"),
                func.sum(func.cast(UsageEvent.success, Integer)).label("successful"),
                func.sum(func.cast(UsageEvent.billable, Integer)).label("documents"),
            )
            .where(
                UsageEvent.organization_id == organization_id,
                UsageEvent.created_at >= since,
            )
            .group_by(day)
            .order_by(day)
        )
        observed = {
            row.day: {
                "day": row.day,
                "requests": int(row.requests or 0),
                "successful": int(row.successful or 0),
                "failed": int(row.requests or 0) - int(row.successful or 0),
                "documents": int(row.documents or 0),
            }
            for row in result.all()
        }

        end = (until or dt.datetime.now(dt.UTC)).date()
        start = since.date()
        series: list[dict[str, object]] = []
        cursor = start
        while cursor <= end:
            key = cursor.isoformat()
            series.append(
                observed.get(
                    key,
                    {
                        "day": key,
                        "requests": 0,
                        "successful": 0,
                        "failed": 0,
                        "documents": 0,
                    },
                )
            )
            cursor += dt.timedelta(days=1)
        return series

    async def recent_events(
        self, organization_id: str, *, limit: int = 20, offset: int = 0
    ) -> list[UsageEvent]:
        result = await self.session.execute(
            select(UsageEvent)
            .where(UsageEvent.organization_id == organization_id)
            .order_by(UsageEvent.created_at.desc())
            .limit(min(limit, 200))
            .offset(offset)
        )
        return list(result.scalars().all())
