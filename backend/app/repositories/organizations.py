from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    slug = _SLUG_STRIP.sub("-", name.lower()).strip("-")
    return slug[:100] or "org"


class OrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, organization_id: str) -> Organization | None:
        return await self.session.get(Organization, organization_id)

    async def get_by_slug(self, slug: str) -> Organization | None:
        result = await self.session.execute(
            select(Organization).where(Organization.slug == slug)
        )
        return result.scalar_one_or_none()

    async def create(self, *, name: str, slug: str | None = None) -> Organization:
        base = slug or slugify(name)
        candidate = base
        suffix = 1
        while await self.get_by_slug(candidate) is not None:
            suffix += 1
            candidate = f"{base}-{suffix}"
        organization = Organization(name=name, slug=candidate)
        self.session.add(organization)
        await self.session.flush()
        return organization
