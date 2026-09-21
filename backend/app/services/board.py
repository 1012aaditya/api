"""The firm's month as one picture.

A CA does not think "show me cases filtered by status". They think *who is
still pending, who is stuck, who is ready* — which is a picture, and until
now it had to be reassembled by clicking between five pages.

So every client becomes a card, and the card's **zone is derived from what
is actually true**, never from where somebody dragged it. Position that can
disagree with state is a lie the interface tells, and this one cannot: move
a card and it goes back, because the zone is computed here on every read.

The zones, in the order a document travels:

* ``waiting``   — asked, nothing back yet. The agent's problem.
* ``reading``   — something arrived and is being read. Nobody's problem.
* ``needs_you`` — the agent stopped on purpose, or a task is overdue.
* ``ready``     — everything is in; the filing is yours to make.
* ``clear``     — nothing open. Present so the month's shape is honest;
                  a board showing only trouble makes a firm look worse
                  than it is.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CaseStatus,
    Client,
    ComplianceCase,
    ContactState,
    DocumentRequirement,
    DocumentType,
    ExceptionStatus,
    RequirementStatus,
    ReviewException,
    Task,
)

ZONES = ("needs_you", "waiting", "reading", "ready", "clear")

#: Titles the board shows above each column. Kept here rather than in the
#: page so the API and the interface cannot drift into describing the same
#: zone differently.
ZONE_LABELS = {
    "needs_you": "Needs you",
    "waiting": "Waiting on the client",
    "reading": "Arrived, being read",
    "ready": "Ready to file",
    "clear": "Nothing open",
}

ZONE_NOTES = {
    "needs_you": "The agent stopped here on purpose.",
    "waiting": "Asked, nothing back yet.",
    "reading": "Something came in and is being read.",
    "ready": "Everything is in. The filing is yours.",
    "clear": "No open work this period.",
}


@dataclass
class Card:
    client_id: str
    name: str
    zone: str
    #: One line saying why this card is where it is. The whole point of the
    #: board is that a person can tell at a glance, without opening it.
    reason: str
    case_id: str | None = None
    period: str | None = None
    deadline: dt.date | None = None
    days_left: int | None = None
    contact_state: str = ContactState.NOT_CONTACTED
    last_contacted_at: dt.datetime | None = None
    last_response_at: dt.datetime | None = None
    #: What this client still owes, in words a CA uses.
    outstanding: list[str] = field(default_factory=list)
    exceptions: int = 0
    tasks_overdue: int = 0
    #: False when the firm has switched automation off for them, or they
    #: asked not to be contacted. Shown, because a card that looks idle for
    #: that reason is not the same as one nobody has got to.
    automated: bool = True


@dataclass
class Board:
    cards: list[Card] = field(default_factory=list)
    generated_at: dt.datetime | None = None

    def counts(self) -> dict[str, int]:
        return {zone: sum(1 for c in self.cards if c.zone == zone) for zone in ZONES}


def _days_left(deadline: dt.date | None, *, now: dt.datetime) -> int | None:
    return (deadline - now.date()).days if deadline else None


async def _by_client(session: AsyncSession, query) -> dict[str, int]:
    """{client_id: count} for a grouped count query."""
    return {client_id: int(count) for client_id, count in await session.execute(query)}


async def build_board(
    session: AsyncSession, organization_id: str, *, now: dt.datetime
) -> Board:
    """Every client, placed by what is true about them right now."""
    clients = list(
        (
            await session.execute(
                select(Client)
                .where(Client.organization_id == organization_id)
                .order_by(Client.name)
            )
        ).scalars()
    )
    if not clients:
        return Board(generated_at=now)

    # The most pressing open case per client. One query rather than one per
    # card: a firm with 400 clients would otherwise make 400 round trips to
    # draw a single screen.
    cases = list(
        (
            await session.execute(
                select(ComplianceCase)
                .where(
                    ComplianceCase.organization_id == organization_id,
                    ComplianceCase.status != CaseStatus.COMPLETED,
                )
                .order_by(ComplianceCase.deadline.asc().nullslast())
            )
        ).scalars()
    )
    by_client: dict[str, ComplianceCase] = {}
    for case in cases:
        by_client.setdefault(case.client_id, case)

    outstanding: dict[str, list[str]] = {}
    if cases:
        rows = await session.execute(
            select(DocumentRequirement.case_id, DocumentRequirement.document_type).where(
                DocumentRequirement.case_id.in_([c.id for c in cases]),
                DocumentRequirement.status.in_(
                    (RequirementStatus.MISSING, RequirementStatus.REQUESTED)
                ),
            )
        )
        for case_id, document_type in rows:
            outstanding.setdefault(case_id, []).append(DocumentType.label(document_type))

    exception_counts = await _by_client(
        session,
        select(ReviewException.client_id, func.count())
        .where(
            ReviewException.organization_id == organization_id,
            ReviewException.status.in_(
                (ExceptionStatus.OPEN, ExceptionStatus.IN_REVIEW)
            ),
            ReviewException.client_id.is_not(None),
        )
        .group_by(ReviewException.client_id),
    )

    overdue = await _by_client(
        session,
        select(Task.client_id, func.count())
        .where(
            Task.organization_id == organization_id,
            Task.completed_at.is_(None),
            Task.due_at.is_not(None),
            Task.due_at < now,
            Task.client_id.is_not(None),
        )
        .group_by(Task.client_id),
    )

    board = Board(generated_at=now)
    for client in clients:
        board.cards.append(
            _place(
                client,
                case=by_client.get(client.id),
                outstanding=outstanding.get(
                    by_client.get(client.id).id if by_client.get(client.id) else "", []
                ),
                exceptions=exception_counts.get(client.id, 0),
                tasks_overdue=overdue.get(client.id, 0),
                now=now,
            )
        )
    return board


def _place(
    client: Client,
    *,
    case: ComplianceCase | None,
    outstanding: list[str],
    exceptions: int,
    tasks_overdue: int,
    now: dt.datetime,
) -> Card:
    """Which zone, and the one line explaining it.

    Order matters: a client can be several of these at once, and the board
    should show the most demanding thing rather than the tidiest. Something
    a person has to do beats something the agent is still handling.
    """
    card = Card(
        client_id=client.id,
        name=client.business_name or client.name,
        zone="clear",
        reason="",
        case_id=case.id if case else None,
        period=case.period if case else None,
        deadline=case.deadline if case else None,
        days_left=_days_left(case.deadline if case else None, now=now),
        contact_state=client.contact_state,
        last_contacted_at=client.last_contacted_at,
        last_response_at=client.last_response_at,
        outstanding=sorted(outstanding),
        exceptions=exceptions,
        tasks_overdue=tasks_overdue,
        automated=client.allow_automated_contact,
    )

    if exceptions:
        card.zone = "needs_you"
        card.reason = (
            "Something needs a person" if exceptions == 1
            else f"{exceptions} things need a person"
        )
        return card

    if tasks_overdue:
        card.zone = "needs_you"
        card.reason = (
            "A task is overdue" if tasks_overdue == 1
            else f"{tasks_overdue} tasks are overdue"
        )
        return card

    if case is None:
        card.zone = "clear"
        card.reason = "No open case."
        return card

    if case.status == CaseStatus.ESCALATED:
        card.zone = "needs_you"
        card.reason = "Chased as far as the agent may go."
        return card

    if case.status == CaseStatus.READY:
        card.zone = "ready"
        card.reason = f"{case.period} is ready to file."
        return card

    if not outstanding:
        # Nothing missing, not yet marked ready: the documents are in and
        # being read.
        card.zone = "reading"
        card.reason = "Everything has arrived and is being read."
        return card

    if not client.allow_automated_contact:
        card.zone = "needs_you"
        card.reason = (
            client.automation_paused_reason
            or "Automated messages are off for this client."
        )
        return card

    if client.contact_state == ContactState.NOT_CONTACTED:
        card.zone = "waiting"
        card.reason = "Not asked yet."
        return card

    if client.contact_state == ContactState.COMMITTED:
        card.zone = "waiting"
        card.reason = "They said they would send it."
        return card

    card.zone = "waiting"
    card.reason = (
        f"Waiting on {outstanding[0].lower()}"
        if len(outstanding) == 1
        else f"Waiting on {len(outstanding)} documents"
    )
    return card
