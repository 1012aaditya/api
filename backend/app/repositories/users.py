from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.email == email.strip().lower())
        )
        return result.scalar_one_or_none()

    async def get(self, organization_id: str, user_id: str) -> User | None:
        result = await self.session.execute(
            select(User).where(
                User.id == user_id, User.organization_id == organization_id
            )
        )
        return result.scalar_one_or_none()

    async def list_for_organization(self, organization_id: str) -> list[User]:
        result = await self.session.execute(
            select(User)
            .where(User.organization_id == organization_id)
            .order_by(User.created_at)
        )
        return list(result.scalars().all())

    async def count_active_owners(self, organization_id: str) -> int:
        from app.models import Role

        result = await self.session.execute(
            select(func.count())
            .select_from(User)
            .where(
                User.organization_id == organization_id,
                User.role == Role.OWNER,
                User.is_active.is_(True),
            )
        )
        return int(result.scalar_one())

    async def create(
        self,
        *,
        organization_id: str,
        email: str,
        password_hash: str,
        full_name: str | None = None,
        role: str = "owner",
    ) -> User:
        user = User(
            organization_id=organization_id,
            email=email.strip().lower(),
            password_hash=password_hash,
            full_name=full_name,
            role=role,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def touch_login(self, user: User, *, now: dt.datetime | None = None) -> None:
        user.last_login_at = now or utcnow()
        await self.session.flush()
