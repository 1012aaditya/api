"""The board: every client as a card, placed by what is true about them."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, authenticate_session, get_request_id
from app.db.base import utcnow
from app.db.session import get_db
from app.schemas.board import BoardOut, CardOut, ZoneOut
from app.schemas.common import SuccessResponse
from app.services.board import ZONE_LABELS, ZONE_NOTES, ZONES, build_board

router = APIRouter(tags=["board"])


@router.get(
    "/board",
    response_model=SuccessResponse[BoardOut],
    summary="The firm's month as one picture",
)
async def board(
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[BoardOut]:
    """One call for the whole screen.

    A firm with 400 clients would otherwise make 400 round trips to draw a
    single view, and a board that takes six seconds to appear is a board
    nobody opens on the 18th.
    """
    built = await build_board(db, auth.organization_id, now=utcnow())
    counts = built.counts()

    return SuccessResponse(
        request_id=request_id,
        data=BoardOut(
            generated_at=built.generated_at,
            zones=[
                ZoneOut(
                    key=zone,
                    label=ZONE_LABELS[zone],
                    note=ZONE_NOTES[zone],
                    count=counts.get(zone, 0),
                )
                for zone in ZONES
            ],
            cards=[
                CardOut(
                    client_id=card.client_id,
                    name=card.name,
                    zone=card.zone,
                    reason=card.reason,
                    case_id=card.case_id,
                    period=card.period,
                    deadline=card.deadline,
                    days_left=card.days_left,
                    contact_state=card.contact_state,
                    last_contacted_at=card.last_contacted_at,
                    last_response_at=card.last_response_at,
                    outstanding=card.outstanding,
                    exceptions=card.exceptions,
                    tasks_overdue=card.tasks_overdue,
                    automated=card.automated,
                )
                for card in built.cards
            ],
        ),
    )
