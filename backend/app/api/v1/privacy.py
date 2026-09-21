"""What this firm holds, and how to make it stop holding it.

A CA partner deciding whether to hand over their clients' financial records
asks three questions: where does this live, who can see it, and can I get it
removed. Prose answers those badly — every product's privacy page says the
same reassuring things. So this answers them from the running deployment:
the retention window actually configured, the storage actually in use,
whether documents actually stay on this hardware, and the firm's own counts.

An uncomfortable configuration produces an uncomfortable answer. A firm
running against a hosted model is told its clients' invoices leave the
network, because the alternative is a page that lies on their behalf.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    authenticate_session,
    get_request_id,
    require_privileged,
)
from app.core.config import Settings, get_settings
from app.core.errors import InvalidRequestError, NotFoundError
from app.db.base import utcnow
from app.db.session import get_db
from app.models import ActorType, Base
from app.repositories.clients import ClientRepository
from app.repositories.operations import AgentEventRepository
from app.schemas.common import ErrorResponse, SuccessResponse
from app.schemas.privacy import ErasureReceiptOut, PrivacyFootprint
from app.services.ai_policy import is_local_endpoint
from app.services.erasure import erase_client

router = APIRouter(prefix="/privacy", tags=["privacy"])


def _count(table: str, organization_id: str):
    t = Base.metadata.tables[table]
    return select(func.count()).select_from(t).where(t.c.organization_id == organization_id)


@router.get(
    "/footprint",
    response_model=SuccessResponse[PrivacyFootprint],
    summary="What this deployment holds about your clients",
)
async def footprint(
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[PrivacyFootprint]:
    """Readable by anyone signed in.

    A junior being able to see what the firm holds is not a privilege; it
    is the minimum a colleague should be able to check for themselves.
    """
    organization_id = auth.organization_id
    now = utcnow()

    documents = Base.metadata.tables["documents"]
    stored = (
        await db.execute(
            select(func.count())
            .select_from(documents)
            .where(
                documents.c.organization_id == organization_id,
                documents.c.storage_key.is_not(None),
            )
        )
    ).scalar_one()
    purged = (
        await db.execute(
            select(func.count())
            .select_from(documents)
            .where(
                documents.c.organization_id == organization_id,
                documents.c.purged_at.is_not(None),
            )
        )
    ).scalar_one()
    next_purge: dt.datetime | None = (
        await db.execute(
            select(func.min(documents.c.retention_expires_at)).where(
                documents.c.organization_id == organization_id,
                documents.c.purged_at.is_(None),
                documents.c.retention_expires_at.is_not(None),
            )
        )
    ).scalar_one_or_none()

    counts = {
        "clients": (await db.execute(_count("clients", organization_id))).scalar_one(),
        "documents_stored": stored,
        "documents_purged": purged,
        "extracted_records": (
            await db.execute(_count("extractions", organization_id))
        ).scalar_one()
        if "organization_id" in Base.metadata.tables["extractions"].columns
        else 0,
        "messages": (await db.execute(_count("messages", organization_id))).scalar_one(),
        "people_with_a_login": (
            await db.execute(_count("users", organization_id))
        ).scalar_one(),
    }

    # Where a document is read decides whether it leaves this hardware, so
    # the answer is derived from the endpoint rather than from an intention.
    #
    # Three states, because the classifier answers "provably private" and
    # not "public". A compose service name like http://ollama:11434 is a
    # model on this very machine and still cannot be proven to be, so
    # calling it "leaves" would frighten a firm about something that is not
    # happening — and calling it "stays" would be the promise the classifier
    # refuses to make. It says neither, and says what to change.
    if not settings.provider_configured:
        locality = "no_model"
        note = (
            "No model is configured, so no document is sent anywhere to be "
            "read. Extraction returns an error rather than a guess."
        )
    elif is_local_endpoint(settings.ai_base_url):
        locality = "stays_here"
        note = (
            f"Documents are read at {settings.ai_base_url}, which is on this "
            "machine or your own network. No client invoice leaves it."
        )
    else:
        locality = "cannot_be_proven"
        note = (
            f"Documents are read at {settings.ai_base_url}. This cannot be "
            "shown to be inside your network from here, so it is not claimed "
            "to be. If that model runs on your own hardware, set AI_BASE_URL "
            "to its IP address and this will confirm it; if it is a hosted "
            "API, your clients' invoices are being sent to it."
        )

    processors: list[dict[str, str]] = []
    if settings.whatsapp_provider == "whatsapp_cloud":
        processors.append(
            {
                "name": "Meta (WhatsApp Business Cloud API)",
                "receives": "Your clients' phone numbers, the messages sent to "
                "them, and any document they send back.",
            }
        )
    if settings.voice_provider == "exotel":
        processors.append(
            {
                "name": "Exotel",
                "receives": "Your clients' phone numbers, and the fact and "
                "duration of a call. The call's audio is theirs, not ours.",
            }
        )
    if locality == "cannot_be_proven":
        processors.append(
            {
                "name": f"Whatever answers at {settings.ai_base_url}",
                "receives": "The page image of every document that reaches the "
                "model tier — your clients' invoices. Listed because it cannot "
                "be shown to be your own hardware, not because it is known not "
                "to be.",
            }
        )
    if settings.storage_backend == "s3":
        processors.append(
            {
                "name": f"Object storage ({settings.s3_endpoint_url or 's3'})",
                "receives": "Every stored document, until it is purged.",
            }
        )

    return SuccessResponse(
        request_id=request_id,
        data=PrivacyFootprint(
            retention_days=auth.retention_days,
            next_purge_at=next_purge,
            generated_at=now,
            storage_backend=settings.storage_backend,
            document_locality=locality,
            locality_note=note,
            model_endpoint=settings.ai_base_url if settings.provider_configured else None,
            extraction_configured=settings.provider_configured,
            whatsapp_provider=settings.whatsapp_provider,
            voice_provider=settings.voice_provider,
            counts=counts,
            processors=processors,
        ),
    )


@router.delete(
    "/clients/{client_id}",
    status_code=status.HTTP_200_OK,
    response_model=SuccessResponse[ErasureReceiptOut],
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
    summary="Erase a client and everything held about them",
)
async def erase(
    client_id: str,
    confirm: str = Query(
        ...,
        description="The client's name, typed exactly. Proof this was meant.",
    ),
    auth: AuthContext = Depends(require_privileged),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[ErasureReceiptOut]:
    """Irreversible. There is no undo and no soft delete.

    Two guards, because this is the most destructive thing the API does.
    Only an owner or an administrator may ask, and they have to type the
    client's name — a confirmation dialog is dismissed by reflex, a name
    has to be read first.
    """
    client = await ClientRepository(db).get(auth.organization_id, client_id)
    if client is None:
        raise NotFoundError("No client with that id exists in this firm.")

    if confirm.strip().casefold() != client.display_name.strip().casefold():
        raise InvalidRequestError(
            f"To erase {client.display_name}, pass their name as `confirm`. "
            "Nothing has been deleted."
        )

    receipt = await erase_client(
        db, organization_id=auth.organization_id, client=client
    )

    # Written after the rows are gone, and holding only counts — an audit
    # entry describing what was erased in any more detail would preserve the
    # very thing somebody asked to have removed.
    await AgentEventRepository(db).record(
        organization_id=auth.organization_id,
        actor_type=ActorType.USER,
        actor_id=auth.user.id if auth.user else None,
        action="client.erased",
        summary=f"{receipt.client_name} and everything held about them was erased",
        details=receipt.as_details(),
    )
    await db.commit()

    return SuccessResponse(
        request_id=request_id,
        data=ErasureReceiptOut(
            client_id=receipt.client_id,
            client_name=receipt.client_name,
            rows_deleted=receipt.rows_deleted,
            by_table={k: v for k, v in receipt.deleted.items() if v},
            unlinked={k: v for k, v in receipt.unlinked.items() if v},
            objects_deleted=receipt.objects_deleted,
            objects_failed=len(receipt.objects_failed),
            complete=receipt.complete,
        ),
    )
