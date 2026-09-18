from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models import APIKey


class APIKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_hash(self, key_hash: str) -> APIKey | None:
        """The one deliberately unscoped read in the codebase.

        Authentication cannot be tenant-scoped, because this lookup is what
        establishes the tenant. Every read after it is scoped to the
        organization this row names.
        """
        result = await self.session.execute(select(APIKey).where(APIKey.key_hash == key_hash))
        return result.scalar_one_or_none()

    async def get(self, organization_id: str, key_id: str) -> APIKey | None:
        result = await self.session.execute(
            select(APIKey).where(
                APIKey.id == key_id, APIKey.organization_id == organization_id
            )
        )
        return result.scalar_one_or_none()

    async def list_for_organization(self, organization_id: str) -> list[APIKey]:
        result = await self.session.execute(
            select(APIKey)
            .where(APIKey.organization_id == organization_id)
            .order_by(APIKey.created_at.desc())
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        organization_id: str,
        key_hash: str,
        prefix: str,
        last_four: str,
        name: str = "Default key",
        environment: str = "live",
        created_by_user_id: str | None = None,
        expires_at: dt.datetime | None = None,
        rotated_from_id: str | None = None,
    ) -> APIKey:
        api_key = APIKey(
            organization_id=organization_id,
            key_hash=key_hash,
            prefix=prefix,
            last_four=last_four,
            name=name,
            environment=environment,
            created_by_user_id=created_by_user_id,
            expires_at=expires_at,
            rotated_from_id=rotated_from_id,
        )
        self.session.add(api_key)
        await self.session.flush()
        return api_key

    async def revoke(self, api_key: APIKey, *, now: dt.datetime | None = None) -> APIKey:
        if api_key.revoked_at is None:
            api_key.revoked_at = now or utcnow()
            await self.session.flush()
        return api_key

    async def touch_last_used(self, api_key: APIKey, *, now: dt.datetime | None = None) -> None:
        api_key.last_used_at = now or utcnow()
        await self.session.flush()
