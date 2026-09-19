"""Inbound webhooks from the messaging provider.

Two endpoints, both boring on purpose:

``GET``  — the subscription handshake every provider does once.
``POST`` — the deliveries.

What matters is what happens before the body is trusted (§23):

* **The signature is checked** against a shared secret, in constant time, and
  a request without one is refused when a secret is configured. A webhook URL
  is public; anything that acts on an unverified body is acting on a stranger's
  instructions.
* **The raw bytes are what get verified**, not a re-serialised dict, because
  those are not the same bytes.
* **A 200 is returned even when nothing could be done.** Providers retry on
  anything else, and retrying will not make an unknown phone number known.
  The reason is recorded instead.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_request_id
from app.core.config import Settings, get_settings
from app.core.errors import AuthenticationRequiredError, InvalidRequestError
from app.core.logging import get_logger
from app.core.security import verify_signature
from app.db.session import get_db
from app.providers.messaging.registry import get_provider
from app.repositories.organizations import OrganizationRepository
from app.services.inbound import InboundService

router = APIRouter(prefix="/inbound", tags=["inbound"])
logger = get_logger("docuparse.inbound.webhook")


@router.get(
    "/whatsapp",
    summary="Provider subscription handshake",
    response_class=Response,
)
async def verify_subscription(
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
    settings: Settings = Depends(get_settings),
) -> Response:
    expected = settings.whatsapp_verify_token
    if not expected:
        raise InvalidRequestError("No WhatsApp verify token is configured.")
    if mode != "subscribe" or token != expected:
        raise AuthenticationRequiredError("The verify token did not match.")
    return Response(content=challenge or "", media_type="text/plain")


@router.post(
    "/whatsapp/{organization_id}",
    status_code=status.HTTP_200_OK,
    summary="Receive messages and documents from clients",
)
async def receive_whatsapp(
    organization_id: str,
    request: Request,
    signature: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    request_id: str = Depends(get_request_id),
) -> dict:
    raw = await request.body()

    secret = settings.whatsapp_webhook_secret
    if secret:
        if not signature:
            raise AuthenticationRequiredError("This webhook requires a signature.")
        # Providers prefix the digest; compare only the digest itself.
        provided = signature.split("=", 1)[-1].strip()
        if not verify_signature(raw, secret, provided):
            logger.warning("inbound.bad_signature")
            raise AuthenticationRequiredError("The webhook signature did not verify.")

    organization = await OrganizationRepository(db).get(organization_id)
    if organization is None:
        # Deliberately the same answer as a bad signature would give: a
        # webhook URL should not confirm which organization ids exist.
        raise AuthenticationRequiredError("Unknown webhook destination.")

    try:
        payload = await request.json()
    except ValueError as exc:
        raise InvalidRequestError("The webhook body was not JSON.") from exc

    messages = get_provider().parse_webhook(payload)
    if not messages:
        # Delivery receipts and status updates land here. Normal traffic.
        return {"success": True, "request_id": request_id, "handled": 0}

    service = InboundService(db, settings=settings)
    handled = 0
    skipped: list[str] = []
    for message in messages:
        outcome = await service.handle(
            organization_id=organization_id,
            inbound=message,
            firm_name=organization.name,
        )
        if outcome.handled:
            handled += 1
        elif outcome.reason:
            skipped.append(outcome.reason)

    await db.commit()
    logger.info("inbound.processed", handled=handled, skipped=len(skipped))
    return {
        "success": True,
        "request_id": request_id,
        "handled": handled,
        "skipped": skipped,
    }
