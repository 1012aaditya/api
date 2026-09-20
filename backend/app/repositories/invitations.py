"""Invitations to join a firm.

The token is a credential, so it follows the same rules as an API key: it is
generated here, returned once to the caller, and only its SHA-256 is written
down. A lost invitation is re-issued, never recovered.
"""

from __future__ import annotations

import datetime as dt
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_api_key
from app.db.base import utcnow
from app.models import Invitation, Role

#: A week is long enough for a partner to forward a message, short enough
#: that a link found in an old chat is useless.
DEFAULT_TTL_DAYS = 7


class GeneratedInvitation:
    """The stored row, plus the one-time token to hand over."""

    def __init__(self, invitation: Invitation, token: str) -> None:
        self.invitation = invitation
        self.token = token


class InvitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        organization_id: str,
        email: str,
        role: str = Role.STAFF,
        invited_by_user_id: str | None = None,
        ttl_days: int = DEFAULT_TTL_DAYS,
        now: dt.datetime | None = None,
    ) -> GeneratedInvitation:
        moment = now or utcnow()
        token = secrets.token_urlsafe(32)
        invitation = Invitation(
            organization_id=organization_id,
            email=email.strip().lower(),
            role=role,
            token_hash=hash_api_key(token),
            invited_by_user_id=invited_by_user_id,
            expires_at=moment + dt.timedelta(days=ttl_days),
        )
        self.session.add(invitation)
        await self.session.flush()
        return GeneratedInvitation(invitation, token)

    async def get(self, organization_id: str, invitation_id: str) -> Invitation | None:
        result = await self.session.execute(
            select(Invitation).where(
                Invitation.id == invitation_id,
                Invitation.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def find_by_token(self, token: str) -> Invitation | None:
        """Look one up by the token the invitee presents.

        Not scoped to an organization on purpose: the token *is* the claim to
        an organization, and the caller has no session yet.
        """
        result = await self.session.execute(
            select(Invitation).where(Invitation.token_hash == hash_api_key(token))
        )
        return result.scalar_one_or_none()

    async def open_for_organization(self, organization_id: str) -> list[Invitation]:
        now = utcnow()
        result = await self.session.execute(
            select(Invitation)
            .where(
                Invitation.organization_id == organization_id,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
                Invitation.expires_at > now,
            )
            .order_by(Invitation.created_at.desc())
        )
        return list(result.scalars().all())

    async def revoke(self, invitation: Invitation) -> Invitation:
        invitation.revoked_at = utcnow()
        await self.session.flush()
        return invitation

    async def accept(self, invitation: Invitation, *, user_id: str) -> Invitation:
        invitation.accepted_at = utcnow()
        invitation.accepted_user_id = user_id
        await self.session.flush()
        return invitation
