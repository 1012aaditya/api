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
from app.repositories.clients import RequirementRepository
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


# --- acting from the board ----------------------------------------------
#
# The board is only useful as a single surface if the things a CA does all
# day can be done without leaving it. Two of those are a person overriding
# the machine, and both have to respect the rules the machine follows.


async def test_a_person_can_say_a_document_arrived(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant
):
    """A bank statement handed over at the office is something only a
    person knows."""
    created = await add_client(client, auth_headers, "Handed Over Traders")
    case = await open_case(client, auth_headers, created["id"])
    requirement = case["requirements"][0]

    updated = data(
        await client.patch(
            f"/v1/requirements/{requirement['id']}",
            headers=auth_headers,
            json={"status": "received"},
        )
    )

    assert updated["status"] == "received"


async def test_a_requirement_cannot_be_moved_somewhere_it_may_not_go(
    client: httpx.AsyncClient, auth_headers
):
    """The state machine is not advisory just because a person is asking
    (§29). It says no, and says why."""
    created = await add_client(client, auth_headers, "Illegal Move Traders")
    case = await open_case(client, auth_headers, created["id"])
    requirement = case["requirements"][0]

    response = await client.patch(
        f"/v1/requirements/{requirement['id']}",
        headers=auth_headers,
        json={"status": "valid"},
    )

    assert response.status_code == 400
    assert "cannot become" in response.json()["error"]["message"]


async def test_waiving_a_requirement_unblocks_the_case(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant
):
    """"We don't need that one" is a decision the firm makes, and the board
    should show the effect immediately."""
    created = await add_client(client, auth_headers, "Waived Traders")
    case = await open_case(client, auth_headers, created["id"])

    for requirement in case["requirements"]:
        await client.patch(
            f"/v1/requirements/{requirement['id']}",
            headers=auth_headers,
            json={"status": "waived"},
        )

    card = card_for(await board_for(tenant.organization_id), "Waived Traders")

    assert card.outstanding == []
    assert card.zone in {"reading", "ready"}


async def test_a_person_can_write_their_own_message(
    client: httpx.AsyncClient, auth_headers
):
    created = await add_client(client, auth_headers, "Typed At Traders")

    sent = data(
        await client.post(
            f"/v1/clients/{created['id']}/messages",
            headers=auth_headers,
            json={"body": "Namaste, could you send the bank statement today?"},
        )
    )

    assert sent["direction"] == "outbound"
    assert sent["sent_by_agent"] is False, "the agent was credited with a person's words"


async def test_a_typed_message_still_respects_an_opt_out(
    client: httpx.AsyncClient, auth_headers
):
    """A client who asked not to be contacted is not contacted because
    somebody typed it by hand."""
    created = await add_client(client, auth_headers, "Opted Out Traders")
    await client.patch(
        f"/v1/clients/{created['id']}",
        headers=auth_headers,
        json={"allow_automated_contact": False},
    )

    response = await client.post(
        f"/v1/clients/{created['id']}/messages",
        headers=auth_headers,
        json={"body": "Just checking in."},
    )

    assert response.status_code == 400


async def test_another_firms_client_cannot_be_messaged(
    client: httpx.AsyncClient, auth_headers, other_tenant: Tenant
):
    created = await add_client(client, auth_headers, "Ours Traders")

    signed_in = await client.post(
        "/v1/auth/login",
        json={"email": other_tenant.email, "password": other_tenant.password},
    )
    stranger = {"Authorization": f"Bearer {data(signed_in)['access_token']}"}

    response = await client.post(
        f"/v1/clients/{created['id']}/messages",
        headers=stranger,
        json={"body": "hello"},
    )

    assert response.status_code == 404


# --- editing the filing itself, from the board -------------------------
#
# The board is where the work happens, so the things a CA changes about a
# filing have to be changeable there: one more document to ask for, one
# that should never have been on the list, the date moving, and somebody
# saying the return has actually been filed.


async def case_for(client: httpx.AsyncClient, headers, name: str, **extra):
    created = await add_client(client, headers, name)
    return await open_case(client, headers, created["id"], **extra), created


async def test_a_filing_can_be_asked_for_one_more_document(
    client: httpx.AsyncClient, auth_headers
):
    case, _ = await case_for(client, auth_headers, "Extra Docs Traders")

    updated = data(
        await client.post(
            f"/v1/cases/{case['id']}/requirements",
            headers=auth_headers,
            json={"document_type": "tds_certificate"},
        )
    )

    assert "TDS certificate" in [r["label"] for r in updated["requirements"]]
    assert "TDS certificate" in updated["outstanding"]


async def test_the_same_document_cannot_be_asked_for_twice(
    client: httpx.AsyncClient, auth_headers
):
    case, _ = await case_for(client, auth_headers, "Twice Traders")
    existing = case["requirements"][0]["document_type"]

    response = await client.post(
        f"/v1/cases/{case['id']}/requirements",
        headers=auth_headers,
        json={"document_type": existing},
    )

    assert response.status_code == 400
    assert "already asks for" in response.json()["error"]["message"]


async def test_an_unknown_document_type_is_refused_by_name(
    client: httpx.AsyncClient, auth_headers
):
    case, _ = await case_for(client, auth_headers, "Unknown Type Traders")

    response = await client.post(
        f"/v1/cases/{case['id']}/requirements",
        headers=auth_headers,
        json={"document_type": "horoscope"},
    )

    assert response.status_code == 400
    assert "horoscope" in response.json()["error"]["message"]


async def test_a_requirement_nothing_arrived_against_can_be_removed(
    client: httpx.AsyncClient, auth_headers
):
    case, _ = await case_for(client, auth_headers, "Remove Traders")
    target = case["requirements"][0]

    updated = data(
        await client.delete(
            f"/v1/requirements/{target['id']}", headers=auth_headers
        )
    )

    assert target["id"] not in [r["id"] for r in updated["requirements"]]


async def test_a_requirement_that_has_been_received_is_not_deleted(
    client: httpx.AsyncClient, auth_headers
):
    """Deleting it would quietly erase what happened. "Not needed" is the
    honest way to close a requirement a document already arrived for."""
    case, _ = await case_for(client, auth_headers, "Arrived Traders")
    target = case["requirements"][0]
    await client.patch(
        f"/v1/requirements/{target['id']}",
        headers=auth_headers,
        json={"status": "received"},
    )

    response = await client.delete(
        f"/v1/requirements/{target['id']}", headers=auth_headers
    )

    assert response.status_code == 400
    assert "not needed" in response.json()["error"]["message"]


async def test_a_filing_can_be_marked_filed_and_unfiled(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant
):
    """Filing happens on a government portal, outside this system, so a
    person has to say it happened — and be able to take it back."""
    case, created = await case_for(client, auth_headers, "Filed Traders")

    filed = data(
        await client.patch(
            f"/v1/cases/{case['id']}", headers=auth_headers, json={"filed": True}
        )
    )
    assert filed["status"] == "completed"
    card = card_for(await board_for(tenant.organization_id), "Filed Traders")
    assert card.zone == "clear"

    reopened = data(
        await client.patch(
            f"/v1/cases/{case['id']}", headers=auth_headers, json={"filed": False}
        )
    )
    # Back under the derived rule: blocked, because documents are missing.
    assert reopened["status"] == "blocked"


async def test_moving_a_deadline_moves_the_documents_with_it(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant
):
    """A requirement left on the old date would have the agent chasing to
    a deadline the firm has already moved."""
    case, _ = await case_for(
        client, auth_headers, "Deadline Traders", deadline="2026-10-20"
    )

    updated = data(
        await client.patch(
            f"/v1/cases/{case['id']}",
            headers=auth_headers,
            json={"deadline": "2026-10-25"},
        )
    )

    assert updated["deadline"] == "2026-10-25"
    async with get_session_factory()() as session:
        rows = await RequirementRepository(session).for_case(
            tenant.organization_id, case["id"]
        )
    assert {row.deadline.isoformat() for row in rows} == {"2026-10-25"}


async def test_a_case_change_with_nothing_in_it_is_refused(
    client: httpx.AsyncClient, auth_headers
):
    case, _ = await case_for(client, auth_headers, "Empty Patch Traders")

    response = await client.patch(
        f"/v1/cases/{case['id']}", headers=auth_headers, json={}
    )

    assert response.status_code == 400


async def test_another_firms_case_cannot_be_edited(
    client: httpx.AsyncClient, auth_headers, other_tenant: Tenant
):
    case, _ = await case_for(client, auth_headers, "Ours Filing Traders")
    signed_in = await client.post(
        "/v1/auth/login",
        json={"email": other_tenant.email, "password": other_tenant.password},
    )
    stranger = {"Authorization": f"Bearer {data(signed_in)['access_token']}"}

    assert (
        await client.patch(
            f"/v1/cases/{case['id']}", headers=stranger, json={"filed": True}
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/v1/cases/{case['id']}/requirements",
            headers=stranger,
            json={"document_type": "pan"},
        )
    ).status_code == 404
