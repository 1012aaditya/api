"""Exceptions, tasks, the audit trail, and the agent's policy."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models import (
    ActorType,
    AgentEvent,
    AgentPolicy,
    ExceptionStatus,
    ReviewException,
    Severity,
    Task,
    TaskStatus,
)


class ExceptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, organization_id: str, exception_id: str) -> ReviewException | None:
        result = await self.session.execute(
            select(ReviewException).where(
                ReviewException.id == exception_id,
                ReviewException.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_organization(
        self,
        organization_id: str,
        *,
        status: str | None = ExceptionStatus.OPEN,
        client_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ReviewException]:
        query = select(ReviewException).where(
            ReviewException.organization_id == organization_id
        )
        if status:
            query = query.where(ReviewException.status == status)
        if client_id:
            query = query.where(ReviewException.client_id == client_id)
        query = (
            query.order_by(ReviewException.created_at.desc())
            .limit(min(limit, 500))
            .offset(offset)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def open_count(self, organization_id: str) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(ReviewException)
            .where(
                ReviewException.organization_id == organization_id,
                ReviewException.status.notin_(tuple(ExceptionStatus.CLOSED)),
            )
        )
        return int(result.scalar_one())

    async def open_for_case(self, organization_id: str, case_id: str) -> list[ReviewException]:
        result = await self.session.execute(
            select(ReviewException).where(
                ReviewException.organization_id == organization_id,
                ReviewException.case_id == case_id,
                ReviewException.status.notin_(tuple(ExceptionStatus.CLOSED)),
            )
        )
        return list(result.scalars().all())

    async def raise_exception(
        self,
        *,
        organization_id: str,
        type: str,
        message: str,
        dedupe_key: str,
        severity: str = Severity.WARNING,
        client_id: str | None = None,
        case_id: str | None = None,
        document_id: str | None = None,
        requirement_id: str | None = None,
        details: dict | None = None,
    ) -> ReviewException:
        """File an exception, or refresh the one already filed for this finding.

        Running the same check twice must not give the reviewer two identical
        rows to close. ``dedupe_key`` is the identity of the *finding*, not of
        the run that found it.

        A finding that reappears after being resolved reopens: the problem came
        back, so somebody should look again.
        """
        existing = (
            await self.session.execute(
                select(ReviewException).where(
                    ReviewException.organization_id == organization_id,
                    ReviewException.dedupe_key == dedupe_key,
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.message = message
            existing.severity = severity
            existing.details = details or {}
            if existing.status in ExceptionStatus.CLOSED:
                existing.status = ExceptionStatus.OPEN
                existing.resolved_at = None
                existing.resolution_note = None
                await self._record(existing, "exception.reopened", f"Came back: {message}")
            # An open finding seen again is the same finding. Recording it
            # every time would bury the timeline in repeats of one problem.
            await self.session.flush()
            return existing

        item = ReviewException(
            organization_id=organization_id,
            client_id=client_id,
            case_id=case_id,
            document_id=document_id,
            requirement_id=requirement_id,
            type=type,
            severity=severity,
            message=message,
            details=details or {},
            dedupe_key=dedupe_key,
        )
        self.session.add(item)
        await self.session.flush()
        await self._record(item, "exception.raised", message)
        return item

    async def _record(self, item: ReviewException, action: str, summary: str) -> None:
        """Put the finding on the timeline.

        An exception is something the system did — it stopped and asked for a
        person. The firm should see that in the same feed as the messages, not
        only by opening the review queue (§J, §29).
        """
        await AgentEventRepository(self.session).record(
            organization_id=item.organization_id,
            client_id=item.client_id,
            case_id=item.case_id,
            action=action,
            summary=summary,
            entity_type="exception",
            entity_id=item.id,
            details={"type": item.type, "severity": item.severity},
        )

    async def resolve(
        self,
        item: ReviewException,
        *,
        status: str,
        note: str | None,
        user_id: str | None,
    ) -> ReviewException:
        item.status = status
        item.resolution_note = note
        item.resolved_by_user_id = user_id
        item.resolved_at = utcnow()
        await self.session.flush()
        return item


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, organization_id: str, task_id: str) -> Task | None:
        result = await self.session.execute(
            select(Task).where(
                Task.id == task_id, Task.organization_id == organization_id
            )
        )
        return result.scalar_one_or_none()

    async def list_for_organization(
        self,
        organization_id: str,
        *,
        status: str | None = TaskStatus.OPEN,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Task]:
        query = select(Task).where(Task.organization_id == organization_id)
        if status:
            query = query.where(Task.status == status)
        query = (
            query.order_by(Task.due_at.is_(None), Task.due_at, Task.created_at.desc())
            .limit(min(limit, 500))
            .offset(offset)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def overdue_count(self, organization_id: str, *, now: dt.datetime | None = None) -> int:
        moment = now or utcnow()
        result = await self.session.execute(
            select(func.count())
            .select_from(Task)
            .where(
                Task.organization_id == organization_id,
                Task.status.notin_(tuple(TaskStatus.CLOSED)),
                Task.due_at.is_not(None),
                Task.due_at < moment,
            )
        )
        return int(result.scalar_one())

    async def create(self, organization_id: str, **fields: object) -> Task:
        task = Task(organization_id=organization_id, **fields)  # type: ignore[arg-type]
        self.session.add(task)
        await self.session.flush()
        return task


class AgentEventRepository:
    """The timeline. Append-only: nothing here is updated or deleted."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        organization_id: str,
        action: str,
        summary: str,
        actor_type: str = ActorType.AGENT,
        actor_id: str | None = None,
        client_id: str | None = None,
        case_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        details: dict | None = None,
        run_id: str | None = None,
        created_at: dt.datetime | None = None,
    ) -> AgentEvent:
        event = AgentEvent(
            organization_id=organization_id,
            client_id=client_id,
            case_id=case_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            summary=summary,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
            run_id=run_id,
            # Stamped from the caller's clock when it supplies one, so that an
            # event and the window that counts it agree. The daily cap reads
            # these rows; a cap counted on a different clock counts nothing.
            **({"created_at": created_at} if created_at is not None else {}),
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def timeline(
        self,
        organization_id: str,
        *,
        client_id: str | None = None,
        case_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentEvent]:
        query = select(AgentEvent).where(AgentEvent.organization_id == organization_id)
        if client_id:
            query = query.where(AgentEvent.client_id == client_id)
        if case_id:
            query = query.where(AgentEvent.case_id == case_id)
        query = (
            query.order_by(AgentEvent.created_at.desc())
            .limit(min(limit, 500))
            .offset(offset)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def count_since(
        self, organization_id: str, action: str, *, since: dt.datetime
    ) -> int:
        """How many of this action today — the per-day caps read this."""
        result = await self.session.execute(
            select(func.count())
            .select_from(AgentEvent)
            .where(
                AgentEvent.organization_id == organization_id,
                AgentEvent.action == action,
                AgentEvent.created_at >= since,
            )
        )
        return int(result.scalar_one())


class AgentPolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, organization_id: str) -> AgentPolicy | None:
        result = await self.session.execute(
            select(AgentPolicy).where(AgentPolicy.organization_id == organization_id)
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, organization_id: str) -> AgentPolicy:
        policy = await self.get(organization_id)
        if policy is not None:
            return policy
        policy = AgentPolicy(organization_id=organization_id)
        self.session.add(policy)
        await self.session.flush()
        return policy

    async def update(self, organization_id: str, values: dict[str, object]) -> AgentPolicy:
        policy = await self.get_or_create(organization_id)
        for key, value in values.items():
            if value is not None and hasattr(policy, key):
                setattr(policy, key, value)
        await self.session.flush()
        return policy
