"""What a firm used in a month, and what that comes to.

Deliberately a **statement**, not a tax invoice. A tax invoice under the
GST Act has required particulars, a serial number from a maintained series,
and a place-of-supply determination — and getting any of them wrong is the
customer's problem as much as the issuer's. This calculates usage and
charges; whether the resulting document is a valid tax invoice is a question
for the operator's own accountant (§14).

The tax line is shown because a customer who is themselves a CA firm will
look for it, and its absence would be more confusing than its presence. It
is a calculation at a configured rate, labelled as one.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Client, Organization, UsageEvent
from app.services.plans import WARN_AT, Plan, get_plan

#: The rate applied to the tax line. Correct for SaaS supplied within India
#: at the time of writing; the operator confirms it and the treatment of
#: inter-state and reverse-charge supplies with their own accountant.
DEFAULT_TAX_RATE = Decimal("0.18")


def period_bounds(period: str) -> tuple[dt.datetime, dt.datetime]:
    """(start, end) for a 'YYYY-MM' period, end exclusive."""
    year, month = (int(part) for part in period.split("-", 1))
    start = dt.datetime(year, month, 1, tzinfo=dt.UTC)
    last = calendar.monthrange(year, month)[1]
    end = dt.datetime(year, month, last, tzinfo=dt.UTC) + dt.timedelta(days=1)
    return start, end


def current_period(now: dt.datetime) -> str:
    return f"{now.year:04d}-{now.month:02d}"


@dataclass
class Line:
    label: str
    detail: str
    amount: Decimal


@dataclass
class Statement:
    organization_id: str
    organization_name: str
    period: str
    plan: Plan
    #: True while the period is still running, so nobody reads a partial
    #: month as a final bill.
    provisional: bool

    documents_used: int
    documents_included: int
    clients: int

    lines: list[Line] = field(default_factory=list)
    tax_rate: Decimal = DEFAULT_TAX_RATE
    #: Said plainly on the statement itself, not only in the code.
    note: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def subtotal(self) -> Decimal:
        return sum((line.amount for line in self.lines), Decimal("0")).quantize(
            Decimal("0.01")
        )

    @property
    def tax(self) -> Decimal:
        return (self.subtotal * self.tax_rate).quantize(Decimal("0.01"))

    @property
    def total(self) -> Decimal:
        return (self.subtotal + self.tax).quantize(Decimal("0.01"))

    @property
    def overage_documents(self) -> int:
        return self.plan.overage(self.documents_used)

    @property
    def share_of_allowance(self) -> float:
        if self.documents_included <= 0:
            return 0.0
        return self.documents_used / self.documents_included


async def documents_in_period(
    session: AsyncSession, organization_id: str, *, start: dt.datetime, end: dt.datetime
) -> int:
    """Billable documents in a window.

    Counts usage events rather than document rows on purpose: a document
    deleted or erased still consumed the work it is billed for, and the
    erasure keeps the count while breaking the link to the client.
    """
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(UsageEvent)
                .where(
                    UsageEvent.organization_id == organization_id,
                    UsageEvent.billable.is_(True),
                    UsageEvent.created_at >= start,
                    UsageEvent.created_at < end,
                )
            )
        ).scalar_one()
    )


async def build_statement(
    session: AsyncSession,
    organization: Organization,
    *,
    period: str,
    now: dt.datetime,
    tax_rate: Decimal = DEFAULT_TAX_RATE,
) -> Statement:
    start, end = period_bounds(period)
    plan = get_plan(organization.plan)

    used = await documents_in_period(
        session, organization.id, start=start, end=end
    )
    clients = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Client)
                .where(Client.organization_id == organization.id)
            )
        ).scalar_one()
    )

    statement = Statement(
        organization_id=organization.id,
        organization_name=organization.name,
        period=period,
        plan=plan,
        provisional=now < end,
        documents_used=used,
        documents_included=plan.included_documents,
        clients=clients,
        tax_rate=tax_rate,
    )

    if plan.monthly_price > 0:
        statement.lines.append(
            Line(
                label=f"{plan.name} plan",
                detail=f"{plan.included_documents} documents included",
                amount=plan.monthly_price.quantize(Decimal("0.01")),
            )
        )

    overage = plan.overage(used)
    if overage and plan.overage_per_document > 0:
        statement.lines.append(
            Line(
                label="Additional documents",
                detail=f"{overage} beyond the {plan.included_documents} included, "
                f"at ₹{plan.overage_per_document} each",
                amount=plan.usage_charge(used),
            )
        )

    statement.note = (
        "A statement of usage and charges, not a tax invoice. The tax line is "
        f"calculated at {tax_rate:.0%}; confirm the rate and the place-of-supply "
        "treatment with your own accountant before relying on it."
    )

    # Warnings, ordered by how soon they cost somebody money.
    if overage:
        statement.warnings.append(
            f"{overage} documents beyond the {plan.included_documents} included "
            f"this month. Work continues — these are charged at "
            f"₹{plan.overage_per_document} each."
        )
    elif plan.included_documents and statement.share_of_allowance >= WARN_AT:
        remaining = plan.included_documents - used
        statement.warnings.append(
            f"{remaining} of {plan.included_documents} included documents left "
            "this month. Going over does not stop anything; it is charged per "
            "document."
        )

    if clients > plan.included_clients:
        statement.warnings.append(
            f"{clients} clients on a plan that includes {plan.included_clients}. "
            "Adding another will be refused until you move up a plan."
        )
    return statement
