"""The board.

One rule carries the whole design: **a card's zone is derived from what is
true, never from where anyone put it.** A position that can disagree with
state is a lie the interface tells, and on the 18th of the month a firm
reading a lie is a filing missed.

So these test placement, not pixels: given a client in a particular
situation, does the board put them where a CA would expect, and does the
line under their name say why.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest

from app.db.base import utcnow
from app.db.session import get_session_factory
from app.services.board import ZONES, build_board
from tests.conftest import Tenant


def data(response: httpx.Response):
    assert response.status_code < 300, response.text
    return response.json()["data"]


async def board_for(organization_id: str, *, now: dt.datetime | None = None):
    async with get_session_factory()() as session:
        return await build_board(session, organization_id, now=now or utcnow())


def card_for(board, name: str):
    matches = [c for c in board.cards if c.name == name]
    assert matches, f"{name} is not on the board at all"
    return matches[0]


async def add_client(client: httpx.AsyncClient, headers, name: str, **extra):
    return data(
        await client.post(
            "/v1/clients",
            headers=headers,
            json={"name": name, "whatsapp_phone": "+919800000001", **extra},
        )
    )


async def open_case(client: httpx.AsyncClient, headers, client_id: str, **extra):
    return data(
        await client.post(
            "/v1/cases",
            headers=headers,
            json={
                "client_id": client_id,
                "case_type": "gst",
                "period": "2026-09",
                "requirements": ["bank_statement", "gstr_2b"],
                **extra,
            },
        )
    )


# --- every client is somewhere -----------------------------------------


async def test_every_client_lands_in_exactly_one_zone(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant
):
    """A client who is on no card is a client nobody chases."""
    for name in ("Alpha Traders", "Beta Exports", "Gamma Foods"):
        await add_client(client, auth_headers, name)
    first = (await board_for(tenant.organization_id)).cards[0]
    await open_case(client, auth_headers, first.client_id)

    board = await board_for(tenant.organization_id)

    assert len(board.cards) == 3
    assert len({c.client_id for c in board.cards}) == 3, "a client appears twice"
    for card in board.cards:
        assert card.zone in ZONES
        assert card.reason, f"{card.name} has no reason for being in {card.zone}"


async def test_a_client_with_no_open_case_is_clear_not_missing(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant
):
    """A board showing only trouble makes a firm look worse than it is."""
    await add_client(client, auth_headers, "Quiet Traders")

    card = card_for(await board_for(tenant.organization_id), "Quiet Traders")

    assert card.zone == "clear"
    assert "No open case" in card.reason


# --- placement ----------------------------------------------------------


async def test_a_client_who_has_not_been_asked_is_waiting(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant
):
    created = await add_client(client, auth_headers, "Not Asked Yet")
    await open_case(client, auth_headers, created["id"])

    card = card_for(await board_for(tenant.organization_id), "Not Asked Yet")

    assert card.zone == "waiting"
    assert card.reason == "Not asked yet."
    # Opening a GST case applies the standard requirement set, so this is a
    # superset of what was asked for. The labels are what matters: a card
    # reading "gstr_2b" would be the database talking, not the product.
    assert {"Bank statement", "GSTR-2B"} <= set(card.outstanding)
    assert all(label[0].isupper() for label in card.outstanding), card.outstanding


async def test_an_open_exception_beats_everything_else(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant
):
    """A client can be several things at once, and the board shows the most
    demanding — something a person must do beats something the agent is
    still handling."""
    from app.models import ExceptionType, Severity
    from app.repositories.operations import ExceptionRepository

    created = await add_client(client, auth_headers, "Stuck Traders")
    await open_case(client, auth_headers, created["id"])

    async with get_session_factory()() as session:
        await ExceptionRepository(session).raise_exception(
            organization_id=tenant.organization_id,
            client_id=created["id"],
            type=ExceptionType.GSTIN_MISMATCH,
            severity=Severity.HIGH,
            message="Neither GSTIN belongs to this client.",
            dedupe_key=f"gstin:{created['id']}",
        )
        await session.commit()

    card = card_for(await board_for(tenant.organization_id), "Stuck Traders")

    assert card.zone == "needs_you"
    assert card.exceptions == 1


async def test_an_overdue_task_pulls_a_client_to_needs_you(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant
):
    created = await add_client(client, auth_headers, "Overdue Traders")
    await open_case(client, auth_headers, created["id"])
    data(
        await client.post(
            "/v1/tasks",
            headers=auth_headers,
            json={
                "client_id": created["id"],
                "title": "Call them",
                "due_at": (utcnow() - dt.timedelta(days=2)).isoformat(),
            },
        )
    )

    card = card_for(await board_for(tenant.organization_id), "Overdue Traders")

    assert card.zone == "needs_you"
    assert card.tasks_overdue == 1


async def test_a_client_who_opted_out_needs_a_person_not_silence(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant
):
    """Automation off is not the same as nothing to do — somebody has to
    decide how that client gets chased instead."""
    created = await add_client(client, auth_headers, "Opted Out Traders")
    await open_case(client, auth_headers, created["id"])
    await client.patch(
        f"/v1/clients/{created['id']}",
        headers=auth_headers,
        json={"allow_automated_contact": False},
    )

    card = card_for(await board_for(tenant.organization_id), "Opted Out Traders")

    assert card.zone == "needs_you"
    assert card.automated is False


async def test_a_case_with_nothing_outstanding_is_being_read(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant
):
    from app.models import RequirementStatus
    from app.repositories.clients import RequirementRepository

    created = await add_client(client, auth_headers, "Arrived Traders")
    case = await open_case(client, auth_headers, created["id"])

    async with get_session_factory()() as session:
        repository = RequirementRepository(session)
        for requirement in await repository.for_case(tenant.organization_id, case["id"]):
            requirement.status = RequirementStatus.RECEIVED
        await session.commit()

    card = card_for(await board_for(tenant.organization_id), "Arrived Traders")

    assert card.zone == "reading"


# --- through the API ----------------------------------------------------


async def test_the_board_comes_back_in_one_call(
    client: httpx.AsyncClient, auth_headers
):
    """A board that takes six seconds to appear is a board nobody opens."""
    for name in ("One", "Two", "Three"):
        await add_client(client, auth_headers, name)

    board = data(await client.get("/v1/board", headers=auth_headers))

    assert len(board["cards"]) == 3
    assert [z["key"] for z in board["zones"]] == list(ZONES)
    assert all(z["label"] and z["note"] for z in board["zones"])


async def test_the_zone_counts_match_the_cards(
    client: httpx.AsyncClient, auth_headers
):
    for name in ("One", "Two", "Three"):
        await add_client(client, auth_headers, name)

    board = data(await client.get("/v1/board", headers=auth_headers))
    counted = {z["key"]: z["count"] for z in board["zones"]}

    for zone in ZONES:
        assert counted[zone] == sum(1 for c in board["cards"] if c["zone"] == zone)


async def test_a_staff_login_may_open_the_board(
    client: httpx.AsyncClient, auth_headers
):
    from tests.test_team import invite_and_join

    staff = await invite_and_join(client, auth_headers, email="junior@sharma.example")

    assert (await client.get("/v1/board", headers=staff)).status_code == 200


async def test_one_firms_board_holds_no_other_firms_clients(
    client: httpx.AsyncClient, auth_headers, other_tenant: Tenant, tenant: Tenant
):
    await add_client(client, auth_headers, "Ours")

    signed_in = await client.post(
        "/v1/auth/login",
        json={"email": other_tenant.email, "password": other_tenant.password},
    )
    stranger = {"Authorization": f"Bearer {data(signed_in)['access_token']}"}

    theirs = data(await client.get("/v1/board", headers=stranger))

    assert theirs["cards"] == []


@pytest.mark.parametrize("zone", ZONES)
def test_every_zone_has_a_label_and_a_note(zone):
    """A column with no explanation is a column somebody guesses at."""
    from app.services.board import ZONE_LABELS, ZONE_NOTES

    assert ZONE_LABELS[zone]
    assert ZONE_NOTES[zone]
