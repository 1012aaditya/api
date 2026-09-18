"""The OpenAI-compatible provider adapter (§12, §34, §42)."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import ExtractionFailedError, ProviderUnavailableError
from app.pipelines.stages.preprocess import PreparedDocument, PreparedPage
from app.providers.json_utils import extract_json_object
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.registry import build_provider


def make_settings(**overrides) -> Settings:
    base = {
        "ai_provider": "openai_compatible",
        "ai_base_url": "https://provider.invalid/v1",
        "ai_api_key": "test-key",
        "ai_model": "test-model",
        "ai_max_retries": 2,
        "ai_timeout_seconds": 5.0,
    }
    return Settings(**{**base, **overrides})


def make_document() -> PreparedDocument:
    return PreparedDocument(
        pages=[
            PreparedPage(
                page_number=1,
                image_bytes=b"\xff\xd8\xffstub",
                mime_type="image/jpeg",
                width=100,
                height=200,
            )
        ]
    )


def chat_response(content: str, *, usage: dict | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "test-model",
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 200},
        },
    )


def provider_with(handler, **settings_overrides) -> OpenAICompatibleProvider:
    settings = make_settings(**settings_overrides)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleProvider(settings, client=client)


# --- configuration -----------------------------------------------------


def test_the_registry_refuses_to_build_without_credentials() -> None:
    with pytest.raises(ProviderUnavailableError):
        build_provider(make_settings(ai_api_key=None))
    with pytest.raises(ProviderUnavailableError):
        build_provider(make_settings(ai_model=None))


def test_an_unknown_provider_name_is_a_configuration_error() -> None:
    with pytest.raises(ProviderUnavailableError) as excinfo:
        build_provider(make_settings(ai_provider="magic-box"))
    assert "magic-box" in excinfo.value.message


def test_openai_compatible_aliases_all_resolve() -> None:
    for name in ("openai", "vllm", "openrouter", "together", "litellm", "ollama"):
        provider = build_provider(make_settings(ai_provider=name))
        assert isinstance(provider, OpenAICompatibleProvider)


# --- the happy path ----------------------------------------------------


async def test_structured_extraction_parses_the_response() -> None:
    provider = provider_with(lambda _r: chat_response('{"invoice_number": "INV-1"}'))
    result = await provider.extract_structured_data(
        make_document(), system_prompt="sys", user_prompt="usr"
    )
    assert result.data == {"invoice_number": "INV-1"}
    assert result.usage.input_tokens == 1000
    assert result.model == "test-model"


async def test_images_are_sent_as_data_urls() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(request.content))
        return chat_response("{}")

    provider = provider_with(handler)
    await provider.extract_structured_data(
        make_document(), system_prompt="sys", user_prompt="usr"
    )
    content = captured["messages"][1]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert captured["temperature"] == 0


async def test_estimated_cost_uses_the_configured_rates() -> None:
    provider = provider_with(
        lambda _r: chat_response("{}"),
        ai_input_cost_per_mtok="3.00",
        ai_output_cost_per_mtok="15.00",
    )
    result = await provider.extract_structured_data(
        make_document(), system_prompt="s", user_prompt="u"
    )
    # 1000 in @ $3/Mtok + 200 out @ $15/Mtok
    assert float(result.usage.estimated_cost_usd) == pytest.approx(0.006)


async def test_cost_is_none_when_no_rates_are_configured() -> None:
    """Unknown cost must read as unknown, not as free."""
    provider = provider_with(lambda _r: chat_response("{}"))
    result = await provider.extract_structured_data(
        make_document(), system_prompt="s", user_prompt="u"
    )
    assert result.usage.estimated_cost_usd is None


# --- resilience --------------------------------------------------------


async def test_a_transient_error_is_retried_then_succeeds(monkeypatch) -> None:
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="upstream busy")
        return chat_response('{"invoice_number": "INV-2"}')

    provider = provider_with(handler)
    result = await provider.extract_structured_data(
        make_document(), system_prompt="s", user_prompt="u"
    )
    assert calls["n"] == 2
    assert result.data["invoice_number"] == "INV-2"


async def test_retries_are_bounded(monkeypatch) -> None:
    """There is no path here that loops forever (§34)."""
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="still busy")

    provider = provider_with(handler, ai_max_retries=2)
    with pytest.raises(ProviderUnavailableError):
        await provider.extract_structured_data(
            make_document(), system_prompt="s", user_prompt="u"
        )
    assert calls["n"] == 3  # the initial attempt plus two retries


async def test_a_client_error_is_not_retried(monkeypatch) -> None:
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="bad request")

    provider = provider_with(handler)
    with pytest.raises(ProviderUnavailableError):
        await provider.extract_structured_data(
            make_document(), system_prompt="s", user_prompt="u"
        )
    assert calls["n"] == 1


async def test_rejected_credentials_surface_as_unavailable_without_retrying(
    monkeypatch,
) -> None:
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="invalid api key")

    provider = provider_with(handler)
    with pytest.raises(ProviderUnavailableError) as excinfo:
        await provider.extract_structured_data(
            make_document(), system_prompt="s", user_prompt="u"
        )
    assert calls["n"] == 1
    assert "invalid api key" not in excinfo.value.message  # no upstream text leaks


async def test_json_mode_is_dropped_when_the_endpoint_rejects_it() -> None:
    """Self-hosted gateways often lack response_format; degrade, do not fail."""
    seen: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        payload = json.loads(request.content)
        seen.append("response_format" in payload)
        if "response_format" in payload:
            return httpx.Response(400, text="response_format is not supported")
        return chat_response('{"invoice_number": "INV-3"}')

    provider = provider_with(handler)
    result = await provider.extract_structured_data(
        make_document(), system_prompt="s", user_prompt="u"
    )
    assert seen == [True, False]
    assert result.data["invoice_number"] == "INV-3"
    assert provider.capabilities().supports_json_mode is False


async def test_a_network_failure_surfaces_as_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("asyncio.sleep", _no_sleep)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    provider = provider_with(handler)
    with pytest.raises(ProviderUnavailableError):
        await provider.extract_structured_data(
            make_document(), system_prompt="s", user_prompt="u"
        )


async def test_prose_instead_of_json_is_an_extraction_failure() -> None:
    """The one thing we must never do is make something up instead."""
    provider = provider_with(
        lambda _r: chat_response("I'm sorry, I can't read this invoice.")
    )
    with pytest.raises(ExtractionFailedError):
        await provider.extract_structured_data(
            make_document(), system_prompt="s", user_prompt="u"
        )


async def test_a_malformed_envelope_is_an_extraction_failure() -> None:
    provider = provider_with(lambda _r: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(ExtractionFailedError):
        await provider.extract_structured_data(
            make_document(), system_prompt="s", user_prompt="u"
        )


# --- JSON recovery -----------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 2}\n```', {"a": 2}),
        ('Sure!\n```\n{"a": 3}\n```\nHope that helps.', {"a": 3}),
        ('prefix {"a": {"b": "}"}} suffix', {"a": {"b": "}"}}),
        ("no json at all", None),
        ("", None),
        ("[1, 2, 3]", None),
    ],
)
def test_json_recovery(text: str, expected: dict | None) -> None:
    assert extract_json_object(text) == expected


async def _no_sleep(_seconds: float) -> None:
    return None
