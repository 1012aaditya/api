"""What a firm is on, what they used, and what it comes to.

No payment is taken here and none can be: there is no payment provider in
this deployment, and a button that looked like it charged a card while doing
nothing would be the worst version of §42. An operator collects by bank
transfer or UPI and records it themselves. The statement says so rather than
implying a payment flow that does not exist.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, authenticate_session, get_request_id
from app.core.errors import InvalidRequestError
from app.db.base import utcnow
from app.db.session import get_db
from app.schemas.billing import LineOut, PlanOut, StatementOut
from app.schemas.common import ErrorResponse, SuccessResponse
from app.services.billing import build_statement, current_period
from app.services.plans import PLANS, get_plan

router = APIRouter(prefix="/billing", tags=["billing"])

_PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _plan_out(plan, *, current: bool = False) -> PlanOut:
    return PlanOut(
        key=plan.key,
        name=plan.name,
        description=plan.description,
        monthly_price=plan.monthly_price,
        included_documents=plan.included_documents,
        overage_per_document=plan.overage_per_document,
        included_clients=plan.included_clients,
        ceiling_documents=plan.ceiling_documents,
        voice_available=plan.voice_available,
        is_current=current,
    )


@router.get(
    "/plans",
    response_model=SuccessResponse[list[PlanOut]],
    summary="The plans this deployment offers",
)
async def list_plans(
    auth: AuthContext = Depends(authenticate_session),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[PlanOut]]:
    current = get_plan(auth.organization.plan)
    return SuccessResponse(
        request_id=request_id,
        data=[_plan_out(p, current=p.key == current.key) for p in PLANS.values()],
    )


@router.get(
    "/statement",
    response_model=SuccessResponse[StatementOut],
    responses={400: {"model": ErrorResponse}},
    summary="What was used in a month, and what it comes to",
)
async def statement(
    period: str | None = Query(
        default=None, description="YYYY-MM. Defaults to this month."
    ),
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[StatementOut]:
    """Readable by anyone signed in.

    What the firm is spending is not a secret from the people spending it,
    and a junior who can see the allowance running down is a junior who can
    say something before the bill does.
    """
    now = utcnow()
    chosen = period or current_period(now)
    if not _PERIOD.match(chosen):
        raise InvalidRequestError(
            f"{chosen!r} is not a month. Use YYYY-MM, for example {current_period(now)}."
        )

    built = await build_statement(db, auth.organization, period=chosen, now=now)

    return SuccessResponse(
        request_id=request_id,
        data=StatementOut(
            organization_name=built.organization_name,
            period=built.period,
            provisional=built.provisional,
            plan=_plan_out(built.plan, current=True),
            documents_used=built.documents_used,
            documents_included=built.documents_included,
            overage_documents=built.overage_documents,
            clients=built.clients,
            lines=[
                LineOut(label=line.label, detail=line.detail, amount=line.amount)
                for line in built.lines
            ],
            subtotal=built.subtotal,
            tax_rate=built.tax_rate,
            tax=built.tax,
            total=built.total,
            currency="INR",
            note=built.note,
            warnings=built.warnings,
            payment_note=(
                "No payment is collected by this software. Settle by bank "
                "transfer or UPI as agreed with your provider."
            ),
        ),
    )
