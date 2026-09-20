"""Dashboard account endpoints.

These authenticate a human with email + password and issue a short-lived
session token. Machine traffic uses API keys instead (§5) — the two paths do
not share credentials.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import UserContext, get_current_user, get_request_id
from app.core.config import Settings, get_settings
from app.core.errors import ConflictError, InvalidAPIKeyError, InvalidRequestError
from app.core.logging import get_logger
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models import ActorType, Organization, Role, User
from app.repositories.invitations import InvitationRepository
from app.repositories.operations import AgentEventRepository
from app.repositories.organizations import OrganizationRepository
from app.repositories.users import UserRepository
from app.schemas.auth import (
    LoginRequest,
    OrganizationSummary,
    SignupRequest,
    TokenResponse,
    UserProfile,
)
from app.schemas.common import ErrorResponse, SuccessResponse
from app.schemas.team import AcceptInviteIn

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger("docuparse.auth")


def _profile(user: User, organization: Organization) -> UserProfile:
    return UserProfile(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        created_at=user.created_at,
        organization=OrganizationSummary(
            id=organization.id,
            name=organization.name,
            slug=organization.slug,
            plan=organization.plan,
        ),
    )


@router.post(
    "/signup",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessResponse[TokenResponse],
    summary="Create an account and its organization",
)
async def signup(
    payload: SignupRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[TokenResponse]:
    users = UserRepository(db)
    if await users.get_by_email(payload.email) is not None:
        raise ConflictError("An account with that email address already exists.")

    organization = await OrganizationRepository(db).create(
        name=payload.organization_name or f"{payload.email.split('@')[0]}'s organization"
    )
    user = await users.create(
        organization_id=organization.id,
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
    )
    logger.info("auth.signup", organization_id=organization.id, user_id=user.id)

    token = create_access_token(user_id=user.id, organization_id=organization.id)
    return SuccessResponse(
        request_id=request_id,
        data=TokenResponse(
            access_token=token,
            expires_in_seconds=settings.jwt_expire_minutes * 60,
            user=_profile(user, organization),
        ),
    )


@router.post(
    "/accept-invite",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessResponse[TokenResponse],
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    summary="Join a firm you were invited to",
)
async def accept_invite(
    payload: AcceptInviteIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[TokenResponse]:
    """Turn a one-time link into a login.

    Deliberately anonymous: the token is the claim. Every way it can fail —
    unknown, used, withdrawn, expired — gives the same answer, because a
    stranger probing links should not learn which firm a token nearly
    belonged to.
    """
    invitations = InvitationRepository(db)
    invitation = await invitations.find_by_token(payload.token)
    if invitation is None or not invitation.is_open():
        raise InvalidRequestError(
            "That invitation link is not usable. It may have been used "
            "already, withdrawn, or expired — ask for a new one."
        )

    users = UserRepository(db)
    if await users.get_by_email(invitation.email) is not None:
        raise ConflictError("An account with that email address already exists.")

    organization = await OrganizationRepository(db).get(invitation.organization_id)
    if organization is None:
        raise InvalidRequestError("That invitation link is not usable.")

    user = await users.create(
        organization_id=organization.id,
        email=invitation.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=invitation.role,
    )
    await invitations.accept(invitation, user_id=user.id)
    # Accepting hands back a session token, so they are signed in from here.
    # Without this the firm's people list would say "Never" about someone who
    # is looking at the dashboard right now.
    await users.touch_login(user)
    await AgentEventRepository(db).record(
        organization_id=organization.id,
        actor_type=ActorType.USER,
        actor_id=user.id,
        action="team.joined",
        summary=f"{user.email} joined as {Role.LABELS.get(user.role, user.role)}",
        entity_type="user",
        entity_id=user.id,
    )
    await db.commit()
    logger.info("auth.invite_accepted", organization_id=organization.id, user_id=user.id)

    token = create_access_token(user_id=user.id, organization_id=organization.id)
    return SuccessResponse(
        request_id=request_id,
        data=TokenResponse(
            access_token=token,
            expires_in_seconds=settings.jwt_expire_minutes * 60,
            user=_profile(user, organization),
        ),
    )


@router.post(
    "/login",
    response_model=SuccessResponse[TokenResponse],
    summary="Exchange email and password for a session token",
)
async def login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[TokenResponse]:
    users = UserRepository(db)
    user = await users.get_by_email(payload.email)
    # One message for "no such user" and "wrong password" — the difference is
    # not the caller's business.
    if user is None or not user.is_active or not verify_password(
        payload.password, user.password_hash
    ):
        raise InvalidAPIKeyError("Email or password is incorrect.")

    organization = await OrganizationRepository(db).get(user.organization_id)
    if organization is None:
        raise InvalidAPIKeyError("Email or password is incorrect.")

    await users.touch_login(user)
    logger.info("auth.login", organization_id=organization.id, user_id=user.id)

    token = create_access_token(user_id=user.id, organization_id=organization.id)
    return SuccessResponse(
        request_id=request_id,
        data=TokenResponse(
            access_token=token,
            expires_in_seconds=settings.jwt_expire_minutes * 60,
            user=_profile(user, organization),
        ),
    )


@router.get(
    "/me",
    response_model=SuccessResponse[UserProfile],
    summary="The signed-in user and their organization",
)
async def me(
    context: UserContext = Depends(get_current_user),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[UserProfile]:
    return SuccessResponse(
        request_id=request_id, data=_profile(context.user, context.organization)
    )
