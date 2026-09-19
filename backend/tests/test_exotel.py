"""The Exotel voice adapter, against a scripted transport.

Same standing as the WhatsApp adapter: the request shapes follow Exotel's
documented API and nothing here has been run against a real account, which is
said in the README rather than implied away.

One thing these tests exist to pin down: this code cannot control what the
client hears. Exotel plays a flow built in their dashboard. Anything that
implied otherwise — a transcript we did not receive, a script we did not
speak — would be the product lying about a phone call, which is the most
expensive kind of lie it could tell (§28).
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.providers.messaging.exotel import ExotelVoiceProvider
from app.providers.messaging.voice import VoiceUnavailableError, build_provider

SID = "sharmaassociates1"
CALLER_ID = "04446163000"
FLOW_ID = "778899"
TO = "+91 98765-43210"


def settings(**overrides) -> Settings:
    return Settings(
        **{
            "app_env": "development",
            "database_url": "sqlite+aiosqlite:///./unused.db",
            "jwt_secret": "unused-in-this-test",
            "voice_provider": "exotel",
            "exotel_sid": SID,
            "exotel_api_key": "key-not-real",
            "exotel_api_token": "token-not-real",
            "exotel_caller_id": CALLER_ID,
            "exotel_flow_id": FLOW_ID,
            **overrides,
        }
    )


def provider(handler, **overrides) -> ExotelVoiceProvider:
    return ExotelVoiceProvider(
        settings(**overrides),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def connected(sid: str = "call-sid-1", status: str = "queued") -> httpx.Response:
    return httpx.Response(200, json={"Call": {"Sid": sid, "Status": status}})


async def test_a_call_is_placed_through_the_firms_flow() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return connected()

    result = await provider(handler).place_call(
        to=TO, script="Hello, this is the AI assistant...", context={"case": "GST 2026-09"}
    )

    assert result.ok
    assert result.provider_call_id == "call-sid-1"

    request = seen[0]
    assert request.url.path == f"/v1/Accounts/{SID}/Calls/connect.json"
    # On the request, not on a client somebody may swap out. An unauthenticated
    # call is one Exotel refuses; worse, it is one nobody notices writing.
    assert request.headers["Authorization"].startswith("Basic ")

    from urllib.parse import parse_qs

    form = {key: value[0] for key, value in parse_qs(request.content.decode()).items()}
    assert form["From"] == "+919876543210", "the spaces and dashes have to go"
    assert form["CallerId"] == CALLER_ID
    assert FLOW_ID in form["Url"], "the flow decides what the client hears"
    assert "GST 2026-09" in form["CustomField"]


async def test_the_region_picks_the_right_cluster() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return connected()

    await provider(handler).place_call(to=TO, script="hi", context={})
    await provider(handler, exotel_region="sg").place_call(to=TO, script="hi", context={})

    assert hosts == ["api.in.exotel.com", "api.exotel.com"]


async def test_a_refused_call_carries_exotels_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"RestException": {"Status": 400, "Message": "CallerId is not verified"}},
        )

    result = await provider(handler).place_call(to=TO, script="hi", context={})

    assert not result.ok
    assert "CallerId is not verified" in result.error


async def test_nothing_is_dialled_without_a_number() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("a call was placed to nobody")

    result = await provider(handler).place_call(to="", script="hi", context={})
    assert not result.ok


async def test_a_call_accepted_without_an_id_is_not_treated_as_placed() -> None:
    """Without a SID the outcome can never be looked up, so it did not happen."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Call": {"Status": "queued"}})

    result = await provider(handler).place_call(to=TO, script="hi", context={})
    assert not result.ok


@pytest.mark.parametrize(
    "exotel_status,expected",
    [
        ("completed", "completed"),
        ("no-answer", "no_answer"),
        ("busy", "no_answer"),
        ("failed", "failed"),
        ("canceled", "cancelled"),
    ],
)
async def test_the_outcome_is_read_back(exotel_status: str, expected: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v1/Accounts/{SID}/Calls/call-sid-1.json"
        return httpx.Response(
            200,
            json={
                "Call": {
                    "Sid": "call-sid-1",
                    "Status": exotel_status,
                    "Duration": "42",
                    "RecordingUrl": "https://recordings.example/abc.mp3",
                }
            },
        )

    outcome = await provider(handler).get_outcome("call-sid-1")

    assert outcome is not None
    assert outcome.status == expected
    assert outcome.duration_seconds == 42
    assert outcome.transcript is None, (
        "Exotel returns a recording, not words. Inventing a transcript would "
        "put things in a client's mouth."
    )


async def test_an_unreachable_provider_is_not_a_finished_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    assert await provider(handler).get_outcome("call-sid-1") is None
    result = await provider(handler).place_call(to=TO, script="hi", context={})
    assert not result.ok


@pytest.mark.parametrize(
    "missing",
    ["exotel_sid", "exotel_api_key", "exotel_api_token", "exotel_caller_id", "exotel_flow_id"],
)
def test_missing_credentials_refuse_rather_than_fall_back(missing: str) -> None:
    with pytest.raises(VoiceUnavailableError) as raised:
        build_provider(settings(**{missing: None}))
    assert missing.upper() in str(raised.value)


def test_the_adapter_is_selected_by_name() -> None:
    assert build_provider(settings()).name == "exotel"
