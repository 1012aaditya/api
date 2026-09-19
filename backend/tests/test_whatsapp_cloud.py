"""The WhatsApp Cloud API adapter, against a scripted transport.

What this proves: the request shapes match Meta's documented API, the token
never leaves the Authorization header, a refusal comes back as an outcome
rather than an exception, and a client's file is downloaded in the two hops
Meta requires.

What it does not prove: that a message arrives on a phone. Nothing in this
repository has been run against a real WhatsApp Business account, and no test
can stand in for that (§42).
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.providers.messaging.registry import MessagingUnavailableError, build_provider
from app.providers.messaging.whatsapp_cloud import WhatsAppCloudProvider

TOKEN = "EAA-test-token-not-real"
PHONE_ID = "1234567890"
TO = "+91 98765-43210"
NUMBER = "919876543210"


def settings(**overrides) -> Settings:
    return Settings(
        **{
            "app_env": "development",
            "database_url": "sqlite+aiosqlite:///./unused.db",
            "jwt_secret": "unused-in-this-test",
            "whatsapp_provider": "whatsapp_cloud",
            "whatsapp_access_token": TOKEN,
            "whatsapp_phone_number_id": PHONE_ID,
            **overrides,
        }
    )


def provider(handler, **overrides) -> WhatsAppCloudProvider:
    transport = httpx.MockTransport(handler)
    return WhatsAppCloudProvider(
        settings(**overrides), client=httpx.AsyncClient(transport=transport)
    )


def accepted(message_id: str = "wamid.TEST") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "messaging_product": "whatsapp",
            "contacts": [{"wa_id": NUMBER}],
            "messages": [{"id": message_id}],
        },
    )


# --- sending ------------------------------------------------------------


async def test_a_text_message_is_sent_in_the_shape_meta_documents() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return accepted()

    result = await provider(handler).send_message(to=TO, body="Hi, we need your GSTR-2B.")

    assert result.ok
    assert result.provider_message_id == "wamid.TEST"

    request = seen[0]
    assert request.url.path == f"/v21.0/{PHONE_ID}/messages"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"

    import json

    body = json.loads(request.content)
    assert body["messaging_product"] == "whatsapp"
    assert body["to"] == NUMBER, "the plus and the punctuation have to go"
    assert body["type"] == "text"
    assert body["text"]["body"] == "Hi, we need your GSTR-2B."
    assert body["text"]["preview_url"] is False


async def test_the_token_is_not_in_the_body_or_the_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return accepted()

    await provider(handler).send_message(to=TO, body="hello")
    request = seen[0]
    assert TOKEN not in str(request.url)
    assert TOKEN.encode() not in request.content


@pytest.mark.parametrize("to,body", [("", "hello"), (TO, "   ")])
async def test_nothing_unsendable_reaches_the_network(to: str, body: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("this should never have been sent")

    result = await provider(handler).send_message(to=to, body=body)
    assert not result.ok
    assert result.error


async def test_the_24_hour_window_is_explained_in_the_firms_language() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "(#131047) Re-engagement message",
                    "code": 131047,
                    "fbtrace_id": "AbC",
                }
            },
        )

    result = await provider(handler).send_message(to=TO, body="hello")

    assert not result.ok
    assert "24 hours" in result.error
    assert "template" in result.error
    assert result.details["permanent"] is True, "retrying this cannot help"


async def test_a_network_failure_is_an_outcome_not_an_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    result = await provider(handler).send_message(to=TO, body="hello")

    assert not result.ok
    assert result.details.get("permanent") is None, "a network failure may be retried"


async def test_a_success_without_an_id_is_not_treated_as_sent() -> None:
    """Everything downstream keys off the provider's id."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"messaging_product": "whatsapp"})

    result = await provider(handler).send_message(to=TO, body="hello")
    assert not result.ok


async def test_a_template_sends_its_variables_in_order() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.content))
        return accepted()

    result = await provider(handler, whatsapp_template_language="en_US").send_template(
        to=TO,
        template="document_reminder",
        variables={"client": "Marigold Retail", "period": "September"},
    )

    assert result.ok
    body = seen[0]
    assert body["type"] == "template"
    assert body["template"]["name"] == "document_reminder"
    assert body["template"]["language"]["code"] == "en_US"
    assert [p["text"] for p in body["template"]["components"][0]["parameters"]] == [
        "Marigold Retail",
        "September",
    ]


async def test_a_document_is_uploaded_before_it_is_sent() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "media-99"})
        import json

        body = json.loads(request.content)
        assert body["document"]["id"] == "media-99"
        assert body["document"]["filename"] == "summary.pdf"
        return accepted()

    result = await provider(handler).send_document(
        to=TO, content=b"%PDF-1.4 ...", filename="summary.pdf", caption="Your summary"
    )

    assert result.ok
    assert calls == [f"/v21.0/{PHONE_ID}/media", f"/v21.0/{PHONE_ID}/messages"]


# --- receiving ----------------------------------------------------------


async def test_media_is_fetched_in_the_two_hops_meta_requires() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/v21.0/media-1":
            return httpx.Response(
                200,
                json={"url": "https://lookaside.example/download", "mime_type": "application/pdf"},
            )
        assert request.headers["Authorization"] == f"Bearer {TOKEN}", (
            "the download is authenticated too"
        )
        return httpx.Response(200, content=b"%PDF-1.4 the client's invoice")

    content = await provider(handler).fetch_media("media-1")

    assert content == b"%PDF-1.4 the client's invoice"
    assert paths == ["/v21.0/media-1", "/download"]


async def test_a_file_bigger_than_the_limit_is_refused_while_it_downloads() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v21.0/media-1":
            return httpx.Response(200, json={"url": "https://lookaside.example/download"})
        return httpx.Response(200, content=b"x" * 5000)

    with pytest.raises(ValueError):
        await provider(handler, max_file_size_bytes=1000).fetch_media("media-1")


async def test_media_that_cannot_be_resolved_raises() -> None:
    """The caller files an exception for the firm; empty bytes would not."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "not found", "code": 100}})

    with pytest.raises(FileNotFoundError):
        await provider(handler).fetch_media("media-gone")


async def test_the_adapter_reads_the_same_envelope_the_mock_writes() -> None:
    from app.providers.messaging.mock import MockWhatsAppProvider

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("parsing makes no requests")

    payload = MockWhatsAppProvider.webhook_for_document(
        message_id="wamid.1",
        from_phone=NUMBER,
        media_reference="media-1",
        filename="statement.pdf",
    )
    messages = provider(handler).parse_webhook(payload)

    assert len(messages) == 1
    assert messages[0].media_reference == "media-1"
    assert messages[0].filename == "statement.pdf"


async def test_a_quick_reply_button_is_read_as_what_they_pressed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("parsing makes no requests")

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.b",
                                    "from": NUMBER,
                                    "type": "button",
                                    "button": {"text": "Bhej diya"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    messages = provider(handler).parse_webhook(payload)
    assert messages[0].body == "Bhej diya"


async def test_a_delivery_receipt_carries_no_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("parsing makes no requests")

    payload = {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.1"}]}}]}]}
    assert provider(handler).parse_webhook(payload) == []


# --- configuration ------------------------------------------------------


@pytest.mark.parametrize(
    "missing", ["whatsapp_access_token", "whatsapp_phone_number_id"]
)
def test_missing_credentials_refuse_rather_than_fall_back(missing: str) -> None:
    with pytest.raises(MessagingUnavailableError) as raised:
        build_provider(settings(**{missing: None}))
    assert missing.upper() in str(raised.value)


def test_the_adapter_is_selected_by_name() -> None:
    assert build_provider(settings()).name == "whatsapp_cloud"
