"""A firm is more than one login.

Two things are being proved here. One: a colleague can be given access
without anybody sharing a password. Two: what a junior's login cannot reach —
because the interesting failure is not "the button was hidden", it is "the
request was refused".
"""

from __future__ import annotations

import httpx
import pytest

from app.db.session import get_session_factory
from app.models import Invitation, Role
from app.repositories.invitations import InvitationRepository
from tests.conftest import Tenant, create_tenant


def data(response: httpx.Response):
    assert response.status_code < 300, response.text
    return response.json()["data"]


async def sign_in(client: httpx.AsyncClient, email: str, password: str) -> dict[str, str]:
    response = await client.post("/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


async def invite_and_join(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    email: str,
    role: str = Role.STAFF,
    password: str = "a-perfectly-fine-password",
) -> dict[str, str]:
    """The whole flow, as a firm actually does it."""
    invitation = data(
        await client.post(
            "/v1/team/invites", headers=headers, json={"email": email, "role": role}
        )
    )
    joined = data(
        await client.post(
            "/v1/auth/accept-invite",
            json={"token": invitation["token"], "password": password, "full_name": "A Colleague"},
        )
    )
    return {"Authorization": f"Bearer {joined['access_token']}"}


# --- the happy path -----------------------------------------------------


async def test_a_colleague_joins_the_same_firm(
    client: httpx.AsyncClient, auth_headers: dict[str, str], tenant: Tenant
) -> None:
    invitation = data(
        await client.post(
            "/v1/team/invites",
            headers=auth_headers,
            json={"email": "junior@sharma.example", "role": "staff"},
        )
    )
    assert invitation["token"], "the link has to be handed over somehow"
    assert "accept-invite?token=" in invitation["accept_url"]

    joined = data(
        await client.post(
            "/v1/auth/accept-invite",
            json={
                "token": invitation["token"],
                "password": "a-perfectly-fine-password",
                "full_name": "Ravi",
            },
        )
    )
    assert joined["user"]["organization"]["id"] == tenant.organization_id, (
        "they join the firm that invited them, not a new one"
    )
    assert joined["user"]["role"] == "staff"

    team = data(await client.get("/v1/team", headers=auth_headers))
    assert sorted(m["email"] for m in team) == sorted([tenant.email, "junior@sharma.example"])
    assert [m for m in team if m["is_you"]][0]["email"] == tenant.email


async def test_the_new_colleague_can_do_the_daily_work(
    client: httpx.AsyncClient, auth_headers: dict[str, str], tenant: Tenant
) -> None:
    """Staff is not read-only. Chasing documents is the job."""
    staff = await invite_and_join(client, auth_headers, email="junior@sharma.example")

    person = data(
        await client.post("/v1/clients", headers=staff, json={"name": "Kirana Bazaar"})
    )
    case = data(
        await client.post(
            "/v1/cases",
            headers=staff,
            json={"client_id": person["id"], "type": "gst", "period": "2026-09"},
        )
    )
    assert case["status"] == "blocked"
    assert data(await client.get("/v1/command-centre", headers=staff))["clients_total"] == 1
    assert (await client.get("/v1/exceptions", headers=staff)).status_code == 200
    assert (await client.get("/v1/tasks", headers=staff)).status_code == 200


# --- what a junior's login may not reach --------------------------------


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/v1/team/invites", {"email": "another@sharma.example", "role": "staff"}),
        ("GET", "/v1/team/invites", None),
        ("PUT", "/v1/agent/policy", {"allow_voice_calls": True}),
        ("POST", "/v1/api-keys", {"name": "mine"}),
        ("GET", "/v1/api-keys", None),
        (
            "POST",
            "/v1/webhooks",
            {"url": "https://example.com/hook", "events": ["document.processed"]},
        ),
    ],
)
async def test_staff_are_refused_the_firms_controls(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    method: str,
    path: str,
    body: dict | None,
) -> None:
    """A phished junior account must not be able to hand over the firm.

    Hiding the button is not a control; refusing the request is.
    """
    staff = await invite_and_join(client, auth_headers, email="junior@sharma.example")

    response = await client.request(method, path, headers=staff, json=body)
    assert response.status_code == 403, f"{method} {path} allowed a staff login"
    assert response.json()["error"]["code"] == "forbidden"


async def test_an_administrator_may_do_those_things(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    admin = await invite_and_join(
        client, auth_headers, email="partner@sharma.example", role=Role.ADMIN
    )
    assert (
        await client.put("/v1/agent/policy", headers=admin, json={"max_calls_per_day": 5})
    ).status_code == 200
    assert (
        await client.post(
            "/v1/team/invites",
            headers=admin,
            json={"email": "third@sharma.example", "role": "staff"},
        )
    ).status_code == 201


# --- the link is a credential -------------------------------------------


async def test_an_invitation_works_once(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    invitation = data(
        await client.post(
            "/v1/team/invites", headers=auth_headers, json={"email": "junior@sharma.example"}
        )
    )
    body = {"token": invitation["token"], "password": "a-perfectly-fine-password"}
    assert (await client.post("/v1/auth/accept-invite", json=body)).status_code == 201

    again = await client.post("/v1/auth/accept-invite", json=body)
    assert again.status_code in (400, 409), "a used link must not work twice"


async def test_a_withdrawn_invitation_stops_working(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    invitation = data(
        await client.post(
            "/v1/team/invites", headers=auth_headers, json={"email": "junior@sharma.example"}
        )
    )
    assert (
        await client.delete(f"/v1/team/invites/{invitation['id']}", headers=auth_headers)
    ).status_code == 200

    refused = await client.post(
        "/v1/auth/accept-invite",
        json={"token": invitation["token"], "password": "a-perfectly-fine-password"},
    )
    assert refused.status_code == 400
    assert data(await client.get("/v1/team/invites", headers=auth_headers)) == []


async def test_an_expired_invitation_stops_working(
    client: httpx.AsyncClient, auth_headers: dict[str, str], tenant: Tenant
) -> None:
    async with get_session_factory()() as session:
        generated = await InvitationRepository(session).create(
            organization_id=tenant.organization_id,
            email="late@sharma.example",
            ttl_days=-1,
        )
        await session.commit()
        token = generated.token

    refused = await client.post(
        "/v1/auth/accept-invite",
        json={"token": token, "password": "a-perfectly-fine-password"},
    )
    assert refused.status_code == 400


async def test_a_made_up_token_gives_nothing_away(client: httpx.AsyncClient) -> None:
    refused = await client.post(
        "/v1/auth/accept-invite",
        json={"token": "not-a-real-token-at-all", "password": "a-perfectly-fine-password"},
    )
    assert refused.status_code == 400
    assert "firm" not in refused.json()["error"]["message"].lower()


async def test_the_token_is_never_stored(
    client: httpx.AsyncClient, auth_headers: dict[str, str], tenant: Tenant
) -> None:
    invitation = data(
        await client.post(
            "/v1/team/invites", headers=auth_headers, json={"email": "junior@sharma.example"}
        )
    )
    async with get_session_factory()() as session:
        row = await session.get(Invitation, invitation["id"])
    assert row.token_hash != invitation["token"]
    assert len(row.token_hash) == 64

    listed = data(await client.get("/v1/team/invites", headers=auth_headers))
    assert "token" not in listed[0], "a link shown twice is a link that leaked"


# --- a firm cannot lock itself out --------------------------------------


async def test_the_last_owner_cannot_be_switched_off(
    client: httpx.AsyncClient, auth_headers: dict[str, str], tenant: Tenant
) -> None:
    admin = await invite_and_join(
        client, auth_headers, email="partner@sharma.example", role=Role.ADMIN
    )
    refused = await client.patch(
        f"/v1/team/{tenant.user_id}", headers=admin, json={"is_active": False}
    )
    assert refused.status_code == 400
    assert "owner" in refused.json()["error"]["message"].lower()


async def test_the_last_owner_cannot_be_demoted(
    client: httpx.AsyncClient, auth_headers: dict[str, str], tenant: Tenant
) -> None:
    admin = await invite_and_join(
        client, auth_headers, email="partner@sharma.example", role=Role.ADMIN
    )
    refused = await client.patch(
        f"/v1/team/{tenant.user_id}", headers=admin, json={"role": "staff"}
    )
    assert refused.status_code == 400


async def test_nobody_switches_off_their_own_login(
    client: httpx.AsyncClient, auth_headers: dict[str, str], tenant: Tenant
) -> None:
    refused = await client.patch(
        f"/v1/team/{tenant.user_id}", headers=auth_headers, json={"is_active": False}
    )
    assert refused.status_code == 403


async def test_nobody_promotes_themselves(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """An administrator quietly becoming an owner is how a firm loses control."""
    admin_headers = await invite_and_join(
        client, auth_headers, email="partner@sharma.example", role=Role.ADMIN
    )
    team = data(await client.get("/v1/team", headers=admin_headers))
    me = [m for m in team if m["is_you"]][0]

    refused = await client.patch(
        f"/v1/team/{me['id']}", headers=admin_headers, json={"role": "owner"}
    )
    assert refused.status_code == 403


# --- switching somebody off actually stops them -------------------------


async def test_switching_a_colleague_off_ends_their_access_at_once(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Their token is still valid and still must not work.

    The junior who left on Friday cannot still read the client list on
    Monday because nobody thought about token expiry.
    """
    staff = await invite_and_join(client, auth_headers, email="junior@sharma.example")
    assert (await client.get("/v1/clients", headers=staff)).status_code == 200

    team = data(await client.get("/v1/team", headers=auth_headers))
    junior = [m for m in team if m["email"] == "junior@sharma.example"][0]
    data(
        await client.patch(
            f"/v1/team/{junior['id']}", headers=auth_headers, json={"is_active": False}
        )
    )

    after = await client.get("/v1/clients", headers=staff)
    assert after.status_code == 401, "the same token must stop working"
    assert (
        await client.post(
            "/v1/auth/login",
            json={"email": "junior@sharma.example", "password": "a-perfectly-fine-password"},
        )
    ).status_code == 401, "and they cannot sign back in"


# --- one firm's people are its own --------------------------------------


async def test_another_firms_colleague_is_invisible(
    client: httpx.AsyncClient, auth_headers: dict[str, str], tenant: Tenant
) -> None:
    other = await create_tenant("Rival Books Pvt Ltd")

    team = data(await client.get("/v1/team", headers=auth_headers))
    assert [m["email"] for m in team] == [tenant.email]

    refused = await client.patch(
        f"/v1/team/{other.user_id}", headers=auth_headers, json={"role": "staff"}
    )
    assert refused.status_code == 404


async def test_an_email_belongs_to_one_firm(
    client: httpx.AsyncClient, auth_headers: dict[str, str], tenant: Tenant
) -> None:
    clash = await client.post(
        "/v1/team/invites", headers=auth_headers, json={"email": tenant.email}
    )
    assert clash.status_code == 409


async def test_an_owner_cannot_be_invited(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    refused = await client.post(
        "/v1/team/invites",
        headers=auth_headers,
        json={"email": "partner@sharma.example", "role": "owner"},
    )
    assert refused.status_code == 400


async def test_joining_is_on_the_record(
    client: httpx.AsyncClient, auth_headers: dict[str, str], tenant: Tenant
) -> None:
    await invite_and_join(client, auth_headers, email="junior@sharma.example")
    activity = data(await client.get("/v1/agent/activity?limit=50", headers=auth_headers))
    actions = {event["action"] for event in activity}
    assert {"team.invited", "team.joined"} <= actions


async def test_a_role_reads_the_same_way_wherever_it_is_shown(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """One firm's screen should not say "Administrator" in one list and
    "admin" in the next."""
    invitation = data(
        await client.post(
            "/v1/team/invites",
            headers=auth_headers,
            json={"email": "second@sharma.example", "role": Role.ADMIN},
        )
    )
    assert invitation["role_label"] == "Administrator"

    outstanding = data(await client.get("/v1/team/invites", headers=auth_headers))
    assert [row["role_label"] for row in outstanding] == ["Administrator"]

    withdrawn = data(
        await client.delete(f"/v1/team/invites/{invitation['id']}", headers=auth_headers)
    )
    assert withdrawn["role_label"] == "Administrator"


async def test_joining_counts_as_signing_in(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Accepting hands back a session token, so they are signed in. The list
    must not tell the firm "Never" about someone reading the dashboard."""
    await invite_and_join(client, auth_headers, email="junior@sharma.example")

    team = data(await client.get("/v1/team", headers=auth_headers))
    joined = [member for member in team if member["email"] == "junior@sharma.example"][0]
    assert joined["last_login_at"] is not None
