"""Shared FastAPI dependencies: authentication, rate limits, quota."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.context import set_organization_id
from app.core.errors import (
    AuthenticationRequiredError,
    InvalidAPIKeyError,
    QuotaExceededError,
    RateLimitExceededError,
)
from app.core.rate_limit import get_rate_limiter
from app.core.security import decode_access_token, hash_api_key, looks_like_api_key
from app.db.base import utcnow
from app.db.session import get_db
from app.models import APIKey, Organization, User
from app.repositories.api_keys import APIKeyRepository
from app.repositories.organizations import OrganizationRepository
from app.repositories.usage import UsageRepository
from app.repositories.users import UserRepository


@dataclass(frozen=True)
class AuthContext:
    """Who is making this request, and what they are allowed to spend.

    Two kinds of caller resolve to this: an API key (machine traffic) and a
    dashboard session (a signed-in human). Both are scoped to exactly one
    organization, so authorization downstream is identical — only the audit
    trail differs, which is what ``actor`` and ``api_key`` record.
    """

    organization: Organization
    settings: Settings
    api_key: APIKey | None = None
    actor: Literal["api_key", "session"] = "api_key"
    user: User | None = None

    @property
    def organization_id(self) -> str:
        return self.organization.id

    @property
    def api_key_id(self) -> str | None:
        return self.api_key.id if self.api_key else None

    @property
    def usage_event_type(self) -> str:
        """Distinguishes dashboard-driven work from real API traffic (§21)."""
        return "api_request" if self.actor == "api_key" else "dashboard_request"

    @property
    def rate_limit_per_minute(self) -> int:
        return (
            self.organization.rate_limit_per_minute
            or self.settings.default_rate_limit_per_minute
        )

    @property
    def monthly_document_quota(self) -> int:
        return (
            self.organization.monthly_document_quota
            or self.settings.default_monthly_document_quota
        )

    @property
    def retention_days(self) -> int:
        if self.organization.retention_days is not None:
            return self.organization.retention_days
        return self.settings.document_retention_days


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization")
    if not header:
        raise AuthenticationRequiredError()
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationRequiredError(
            "Authorization header must be of the form 'Bearer <token>'."
        )
    return token.strip()


async def authenticate_api_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    """Resolve an API key to its organization, or refuse the request."""
    token = _bearer_token(request)
    if not looks_like_api_key(token):
        # Shape check only — it short-circuits before a database round trip
        # and gives the developer a clearer message than "invalid".
        raise InvalidAPIKeyError(
            "Malformed API key. Keys look like 'dp_live_...' or 'dp_test_...'."
        )

    api_key = await APIKeyRepository(db).get_by_hash(hash_api_key(token))
    if api_key is None or not api_key.is_usable():
        raise InvalidAPIKeyError()

    organization = await OrganizationRepository(db).get(api_key.organization_id)
    if organization is None:
        raise InvalidAPIKeyError()

    set_organization_id(organization.id)
    request.state.organization_id = organization.id
    request.state.api_key_id = api_key.id
    await APIKeyRepository(db).touch_last_used(api_key)
    return AuthContext(
        organization=organization, api_key=api_key, settings=settings, actor="api_key"
    )


async def authenticate_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    """Resolve a dashboard session token to its organization."""
    context = await get_current_user(request, db)
    return AuthContext(
        organization=context.organization,
        settings=settings,
        api_key=None,
        actor="session",
        user=context.user,
    )


async def authenticate_any(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    """Accept either an API key or a dashboard session.

    The dashboard needs to read the same organization-scoped data the API
    exposes, and it holds a session token rather than a key — keys are stored
    hashed and cannot be replayed from the browser. Both credentials resolve
    to one organization, so what follows is identical either way.

    The token's own shape decides which path runs, so this never tries a
    session lookup with an API key or vice versa.
    """
    token = _bearer_token(request)
    if looks_like_api_key(token):
        return await authenticate_api_key(request, db, settings)
    return await authenticate_session(request, db, settings)


async def enforce_rate_limit(
    auth: AuthContext = Depends(authenticate_any),
) -> AuthContext:
    decision = await get_rate_limiter().check(
        f"org:{auth.organization_id}", limit=auth.rate_limit_per_minute, window_seconds=60
    )
    if not decision.allowed:
        raise RateLimitExceededError(
            f"Rate limit of {decision.limit} requests per minute exceeded.",
            retry_after_seconds=decision.retry_after_seconds,
            details={"limit_per_minute": decision.limit},
        )
    return auth


async def enforce_document_quota(
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    """Monthly document allowance. Unlike the rate limiter, this never fails open."""
    now: dt.datetime = utcnow()
    used = await UsageRepository(db).billable_count_this_month(auth.organization_id, now=now)
    quota = auth.monthly_document_quota
    if used >= quota:
        raise QuotaExceededError(
            f"Monthly quota of {quota} documents has been used.",
            details={"quota": quota, "used": used},
        )
    return auth


# --- Dashboard session auth --------------------------------------------


@dataclass(frozen=True)
class UserContext:
    user: User
    organization: Organization


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> UserContext:
    token = _bearer_token(request)
    payload = decode_access_token(token)
    if payload is None:
        raise InvalidAPIKeyError("Session token is invalid or has expired.")

    organization_id = payload.get("org")
    user_id = payload.get("sub")
    if not organization_id or not user_id:
        raise InvalidAPIKeyError("Session token is invalid or has expired.")

    user = await UserRepository(db).get(organization_id, user_id)
    if user is None or not user.is_active:
        raise InvalidAPIKeyError("Session token is invalid or has expired.")

    organization = await OrganizationRepository(db).get(organization_id)
    if organization is None:
        raise InvalidAPIKeyError("Session token is invalid or has expired.")

    set_organization_id(organization.id)
    request.state.organization_id = organization.id
    return UserContext(user=user, organization=organization)
