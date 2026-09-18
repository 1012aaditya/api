"""API key lifecycle: create, list, revoke, rotate (§17).

Every route here is authenticated with a dashboard session token, not with an
API key — a leaked API key must not be able to mint more keys.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import UserContext, get_current_user, get_request_id
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.security import generate_api_key
from app.db.base import utcnow
from app.db.session import get_db
from app.models import APIKey
from app.repositories.api_keys import APIKeyRepository
from app.schemas.api_key import APIKeySummary, CreateAPIKeyRequest, CreatedAPIKey
from app.schemas.common import SuccessResponse

router = APIRouter(prefix="/api-keys", tags=["api-keys"])
logger = get_logger("docuparse.apikeys")


def _summary(api_key: APIKey) -> APIKeySummary:
    return APIKeySummary(
        id=api_key.id,
        name=api_key.name,
        environment=api_key.environment,
        masked_key=f"{api_key.prefix}...{api_key.last_four}",
        created_at=api_key.created_at,
        last_used_at=api_key.last_used_at,
        revoked_at=api_key.revoked_at,
        expires_at=api_key.expires_at,
        active=api_key.is_usable(),
    )


async def _issue(
    db: AsyncSession,
    *,
    context: UserContext,
    name: str,
    environment: str,
    expires_in_days: int | None,
    rotated_from_id: str | None = None,
) -> CreatedAPIKey:
    generated = generate_api_key(environment)  # type: ignore[arg-type]
    expires_at = (
        utcnow() + dt.timedelta(days=expires_in_days) if expires_in_days else None
    )
    api_key = await APIKeyRepository(db).create(
        organization_id=context.organization.id,
        key_hash=generated.key_hash,
        prefix=generated.prefix,
        last_four=generated.last_four,
        name=name,
        environment=environment,
        created_by_user_id=context.user.id,
        expires_at=expires_at,
        rotated_from_id=rotated_from_id,
    )
    # The plaintext is logged nowhere — only the id and the masked display form.
    logger.info(
        "apikey.created",
        organization_id=context.organization.id,
        api_key_id=api_key.id,
        environment=environment,
        rotated_from_id=rotated_from_id,
    )
    return CreatedAPIKey(**_summary(api_key).model_dump(), key=generated.plaintext)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessResponse[CreatedAPIKey],
    summary="Create an API key (the secret is shown once)",
)
async def create_api_key(
    payload: CreateAPIKeyRequest,
    context: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[CreatedAPIKey]:
    created = await _issue(
        db,
        context=context,
        name=payload.name,
        environment=payload.environment,
        expires_in_days=payload.expires_in_days,
    )
    return SuccessResponse(request_id=request_id, data=created)


@router.get(
    "",
    response_model=SuccessResponse[list[APIKeySummary]],
    summary="List this organization's API keys",
)
async def list_api_keys(
    context: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[APIKeySummary]]:
    keys = await APIKeyRepository(db).list_for_organization(context.organization.id)
    return SuccessResponse(request_id=request_id, data=[_summary(k) for k in keys])


@router.delete(
    "/{key_id}",
    response_model=SuccessResponse[APIKeySummary],
    summary="Revoke an API key",
)
async def revoke_api_key(
    key_id: str,
    context: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[APIKeySummary]:
    repo = APIKeyRepository(db)
    api_key = await repo.get(context.organization.id, key_id)
    if api_key is None:
        # A key belonging to another organization is indistinguishable from
        # one that does not exist. Saying "forbidden" would leak its existence.
        raise NotFoundError("No API key with that id exists in this organization.")
    await repo.revoke(api_key)
    logger.info(
        "apikey.revoked", organization_id=context.organization.id, api_key_id=api_key.id
    )
    return SuccessResponse(request_id=request_id, data=_summary(api_key))


@router.post(
    "/{key_id}/rotate",
    response_model=SuccessResponse[CreatedAPIKey],
    summary="Issue a replacement key and revoke the old one",
)
async def rotate_api_key(
    key_id: str,
    context: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[CreatedAPIKey]:
    repo = APIKeyRepository(db)
    existing = await repo.get(context.organization.id, key_id)
    if existing is None:
        raise NotFoundError("No API key with that id exists in this organization.")
    if existing.revoked_at is not None:
        raise ConflictError("That key is already revoked; create a new one instead.")

    created = await _issue(
        db,
        context=context,
        name=existing.name,
        environment=existing.environment,
        expires_in_days=None,
        rotated_from_id=existing.id,
    )
    await repo.revoke(existing)
    return SuccessResponse(request_id=request_id, data=created)
