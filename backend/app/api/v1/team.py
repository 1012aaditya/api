"""The people inside one firm (§18).

A CA practice is three to twenty people sharing one client list. Until this
existed the person who signed up was the only one who could get in, which
left a firm two options: share one password around the office, or have one
person do all the work. Both are bad, and the second is the one this product
exists to prevent.

Two rules shape everything here:

* **An invitation is a credential.** There is no mail server in this
  deployment, so the link is sent by whoever is inviting, however they
  already talk. It is therefore stored hashed, shown once, single-use and
  short-lived — the same treatment an API key gets.
* **A firm can never lock itself out.** The last active owner cannot be
  demoted or switched off, and nobody can switch themselves off by accident.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    authenticate_session,
    get_request_id,
    require_privileged,
)
from app.core.config import Settings, get_settings
from app.core.errors import ConflictError, ForbiddenError, InvalidRequestError, NotFoundError
from app.db.session import get_db
from app.models import ActorType, Role
from app.repositories.invitations import InvitationRepository
from app.repositories.operations import AgentEventRepository
from app.repositories.users import UserRepository
from app.schemas.common import ErrorResponse, SuccessResponse
from app.schemas.team import (
    InvitationCreated,
    InvitationOut,
    InviteIn,
    MemberPatch,
    TeamMemberOut,
)

router = APIRouter(prefix="/team", tags=["team"])


def _role_label(role: str) -> str:
    return Role.LABELS.get(role, role.title())


def _member_out(user, *, you: str | None) -> TeamMemberOut:
    return TeamMemberOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        role_label=_role_label(user.role),
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        is_you=user.id == you,
    )


@router.get(
    "",
    response_model=SuccessResponse[list[TeamMemberOut]],
    summary="Everyone with a login to this firm",
)
async def list_team(
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[TeamMemberOut]]:
    """Readable by anyone signed in.

    Knowing who else can see your clients' documents is not an
    administrator's privilege; it is the minimum a colleague should be able
    to check for themselves.
    """
    users = await UserRepository(db).list_for_organization(auth.organization_id)
    you = auth.user.id if auth.user else None
    return SuccessResponse(
        request_id=request_id, data=[_member_out(user, you=you) for user in users]
    )


@router.post(
    "/invites",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessResponse[InvitationCreated],
    responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    summary="Invite a colleague",
)
async def invite(
    payload: InviteIn,
    auth: AuthContext = Depends(require_privileged),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[InvitationCreated]:
    if payload.role not in Role.ALL:
        raise InvalidRequestError(
            f"{payload.role!r} is not a role. Use one of: "
            f"{', '.join(sorted(Role.ALL))}."
        )
    if payload.role == Role.OWNER:
        raise InvalidRequestError(
            "An owner cannot be invited. Invite them as an administrator and "
            "change the role afterwards, so the change is somebody's decision "
            "on the record."
        )

    email = str(payload.email).strip().lower()
    users = UserRepository(db)
    if await users.get_by_email(email) is not None:
        # The email is unique across the whole deployment, so this is not only
        # "already in this firm" — say so plainly rather than leaking which.
        raise ConflictError(
            f"{email} already has a login. One email address belongs to one firm."
        )

    generated = await InvitationRepository(db).create(
        organization_id=auth.organization_id,
        email=email,
        role=payload.role,
        invited_by_user_id=auth.user.id if auth.user else None,
    )
    await AgentEventRepository(db).record(
        organization_id=auth.organization_id,
        actor_type=ActorType.USER,
        actor_id=auth.user.id if auth.user else None,
        action="team.invited",
        summary=f"{email} was invited as {Role.LABELS.get(payload.role, payload.role)}",
        entity_type="invitation",
        entity_id=generated.invitation.id,
        details={"email": email, "role": payload.role},
    )
    await db.commit()

    invitation = generated.invitation
    return SuccessResponse(
        request_id=request_id,
        data=InvitationCreated(
            id=invitation.id,
            email=invitation.email,
            role=invitation.role,
            role_label=_role_label(invitation.role),
            invited_by=auth.user.email if auth.user else None,
            expires_at=invitation.expires_at,
            created_at=invitation.created_at,
            token=generated.token,
            accept_url=f"{settings.app_url.rstrip('/')}/accept-invite?token={generated.token}",
        ),
    )


@router.get(
    "/invites",
    response_model=SuccessResponse[list[InvitationOut]],
    responses={403: {"model": ErrorResponse}},
    summary="Invitations nobody has used yet",
)
async def list_invites(
    auth: AuthContext = Depends(require_privileged),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[InvitationOut]]:
    rows = await InvitationRepository(db).open_for_organization(auth.organization_id)
    return SuccessResponse(
        request_id=request_id,
        data=[
            InvitationOut(
                id=row.id,
                email=row.email,
                role=row.role,
                role_label=_role_label(row.role),
                expires_at=row.expires_at,
                created_at=row.created_at,
            )
            for row in rows
        ],
    )


@router.delete(
    "/invites/{invitation_id}",
    response_model=SuccessResponse[InvitationOut],
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    summary="Withdraw an invitation",
)
async def revoke_invite(
    invitation_id: str,
    auth: AuthContext = Depends(require_privileged),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[InvitationOut]:
    repository = InvitationRepository(db)
    invitation = await repository.get(auth.organization_id, invitation_id)
    if invitation is None:
        raise NotFoundError("No invitation with that id exists in this firm.")

    await repository.revoke(invitation)
    await AgentEventRepository(db).record(
        organization_id=auth.organization_id,
        actor_type=ActorType.USER,
        actor_id=auth.user.id if auth.user else None,
        action="team.invite_revoked",
        summary=f"The invitation to {invitation.email} was withdrawn",
        entity_type="invitation",
        entity_id=invitation.id,
    )
    await db.commit()
    return SuccessResponse(
        request_id=request_id,
        data=InvitationOut(
            id=invitation.id,
            email=invitation.email,
            role=invitation.role,
            role_label=_role_label(invitation.role),
            expires_at=invitation.expires_at,
            created_at=invitation.created_at,
        ),
    )


@router.patch(
    "/{user_id}",
    response_model=SuccessResponse[TeamMemberOut],
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
    summary="Change what a colleague may do, or switch them off",
)
async def update_member(
    user_id: str,
    payload: MemberPatch,
    auth: AuthContext = Depends(require_privileged),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[TeamMemberOut]:
    users = UserRepository(db)
    member = await users.get(auth.organization_id, user_id)
    if member is None:
        raise NotFoundError("No colleague with that id exists in this firm.")

    me = auth.user.id if auth.user else None
    if payload.role is not None and member.id == me:
        # Otherwise an administrator quietly promotes themselves to owner.
        raise ForbiddenError("Ask a colleague to change your own role.")
    if payload.is_active is False and member.id == me:
        raise ForbiddenError("You cannot switch off your own login.")

    if payload.role is not None:
        if payload.role not in Role.ALL:
            raise InvalidRequestError(f"{payload.role!r} is not a role.")
        if (
            member.role == Role.OWNER
            and payload.role != Role.OWNER
            and await users.count_active_owners(auth.organization_id) <= 1
        ):
            raise InvalidRequestError(
                "This is the firm's only owner. Make somebody else an owner first."
            )
        member.role = payload.role

    if payload.is_active is not None:
        if (
            payload.is_active is False
            and member.role == Role.OWNER
            and await users.count_active_owners(auth.organization_id) <= 1
        ):
            raise InvalidRequestError(
                "This is the firm's only owner. A firm has to keep one."
            )
        member.is_active = payload.is_active

    await AgentEventRepository(db).record(
        organization_id=auth.organization_id,
        actor_type=ActorType.USER,
        actor_id=me,
        action="team.member_changed",
        summary=(
            f"{member.email} is now "
            f"{Role.LABELS.get(member.role, member.role)}"
            f"{'' if member.is_active else ', and switched off'}"
        ),
        entity_type="user",
        entity_id=member.id,
        details={"role": member.role, "is_active": member.is_active},
    )
    await db.commit()
    return SuccessResponse(request_id=request_id, data=_member_out(member, you=me))
