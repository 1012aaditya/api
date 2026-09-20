"""The preflight check, which exists to answer one question honestly:
is each external dependency working *right now*?

The failure mode worth guarding against is not a crash — it is a cheerful
"ok" for something that is not working. An operator runs this before
pointing a customer at the deployment; a check that infers success from a
set variable, or from a response body that did not contain the answer, is
worse than no check at all (§42).
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import preflight  # noqa: E402

from app.core.config import Settings  # noqa: E402


def transport(handler):
    """Patch the module's client so every check talks to a scripted server."""
    return httpx.MockTransport(handler)


@pytest.fixture
def meta(monkeypatch):
    """A stand-in Graph API whose answers each test decides."""
    responses: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        for fragment, response in responses.items():
            if fragment in str(request.url):
                return response
        return httpx.Response(404, json={"error": {"message": "not found", "code": 803}})

    real = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(preflight.httpx, "AsyncClient", patched)
    return responses


def settings(**values) -> Settings:
    base = {
        "whatsapp_provider": "whatsapp_cloud",
        "whatsapp_phone_number_id": "123",
        "whatsapp_access_token": "a-token",
        "whatsapp_api_base": "https://graph.facebook.com",
    }
    return Settings(**{**base, **values})


# --- a success is never inferred ---------------------------------------


async def test_a_dead_token_is_not_reported_as_a_working_number(meta):
    """The failure this was written for. Meta answers 401, and an earlier
    version read the missing fields as defaults and said the number was
    live — which is the one thing a preflight must never do."""
    meta["/123"] = httpx.Response(
        401,
        json={"error": {"message": "Session has expired.", "code": 190}},
    )

    checks = await preflight.check_whatsapp(settings())

    assert checks[0].status == "fail"
    assert "190" in checks[0].detail


async def test_an_answer_without_the_number_is_not_a_working_number(meta):
    """A 200 that does not say which number this is proves nothing. Saying
    "live" here would be inventing the reassurance."""
    meta["/123"] = httpx.Response(200, json={"id": "123"})

    checks = await preflight.check_whatsapp(settings())

    assert checks[0].status == "fail"
    assert "did not say which number" in checks[0].detail


async def test_an_error_body_behind_a_200_is_still_an_error(meta):
    """A gateway in front of Meta may pass the error through with a 200."""
    meta["/123"] = httpx.Response(
        200, json={"error": {"message": "Session has expired.", "code": 190}}
    )

    checks = await preflight.check_whatsapp(settings())

    assert checks[0].status == "fail"


async def test_a_live_number_is_reported_with_what_meta_said(meta):
    meta["fields=display_phone_number"] = httpx.Response(
        200,
        json={
            "display_phone_number": "+91 98000 12345",
            "verified_name": "Sharma & Associates",
            "quality_rating": "GREEN",
        },
    )
    meta["fields=whatsapp_business_account"] = httpx.Response(
        200, json={"whatsapp_business_account": {"id": "waba-1"}}
    )
    meta["message_templates"] = httpx.Response(
        200,
        json={
            "data": [
                {
                    "name": "document_chase",
                    "status": "APPROVED",
                    "components": [
                        {"type": "BODY", "text": "{{1}} {{2}} {{3}} {{4}}"}
                    ],
                }
            ]
        },
    )

    checks = await preflight.check_whatsapp(settings(whatsapp_template_name="document_chase"))

    assert [c.status for c in checks] == ["ok", "ok"]
    assert "+91 98000 12345" in checks[0].detail


# --- the template, which is what makes a first chase possible ----------


@pytest.mark.parametrize(
    "template,expected",
    [
        ({"name": "t", "status": "PENDING", "components": []}, "not APPROVED"),
        ({"name": "t", "status": "REJECTED", "components": []}, "not APPROVED"),
    ],
)
async def test_an_unapproved_template_fails(meta, template, expected):
    """Meta will not deliver it, so the first chase to a silent client
    cannot be sent — that is a failure, not a warning."""
    meta["fields=display_phone_number"] = httpx.Response(
        200, json={"display_phone_number": "+91 1", "verified_name": "F"}
    )
    meta["fields=whatsapp_business_account"] = httpx.Response(
        200, json={"whatsapp_business_account": {"id": "waba-1"}}
    )
    meta["message_templates"] = httpx.Response(200, json={"data": [template]})

    checks = await preflight.check_whatsapp(settings(whatsapp_template_name="t"))

    assert checks[1].status == "fail"
    assert expected in checks[1].detail


async def test_a_template_with_the_wrong_variable_count_fails(meta):
    """WhatsApp fills them positionally and rejects a mismatch at send time
    with error 132000 — i.e. on a real client's chase."""
    meta["fields=display_phone_number"] = httpx.Response(
        200, json={"display_phone_number": "+91 1", "verified_name": "F"}
    )
    meta["fields=whatsapp_business_account"] = httpx.Response(
        200, json={"whatsapp_business_account": {"id": "waba-1"}}
    )
    meta["message_templates"] = httpx.Response(
        200,
        json={
            "data": [
                {
                    "name": "t",
                    "status": "APPROVED",
                    "components": [{"type": "BODY", "text": "Hi {{1}}, send {{2}}."}],
                }
            ]
        },
    )

    checks = await preflight.check_whatsapp(settings(whatsapp_template_name="t"))

    assert checks[1].status == "fail"
    assert "2 variables, not 4" in checks[1].detail


async def test_no_template_at_all_fails(meta):
    meta["fields=display_phone_number"] = httpx.Response(
        200, json={"display_phone_number": "+91 1", "verified_name": "F"}
    )

    checks = await preflight.check_whatsapp(settings(whatsapp_template_name=None))

    assert checks[1].status == "fail"
    assert "no template configured" in checks[1].detail


# --- the model ----------------------------------------------------------


async def test_a_model_that_was_never_pulled_fails(monkeypatch):
    """The most common way extraction fails: the endpoint is up, and the
    model was never pulled."""
    def handler(request):
        return httpx.Response(200, json={"data": [{"id": "llama3:8b"}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        preflight.httpx,
        "AsyncClient",
        lambda *a, **k: real(*a, **{**k, "transport": transport(handler)}),
    )

    check = await preflight.check_model(
        Settings(ai_api_key="k", ai_model="qwen2.5vl:7b", ai_base_url="http://x/v1")
    )

    assert check.status == "fail"
    assert "not among its models" in check.detail


async def test_an_ollama_tag_still_matches(monkeypatch):
    """Ollama answers to the bare name as well as name:tag, so an
    exact-match-only check would call a working setup broken."""
    def handler(request):
        return httpx.Response(200, json={"data": [{"id": "qwen2.5vl:latest"}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        preflight.httpx,
        "AsyncClient",
        lambda *a, **k: real(*a, **{**k, "transport": transport(handler)}),
    )

    check = await preflight.check_model(
        Settings(ai_api_key="k", ai_model="qwen2.5vl:7b", ai_base_url="http://x/v1")
    )

    assert check.status == "ok"


async def test_an_endpoint_that_lists_nothing_is_unverifiable_not_broken(monkeypatch):
    """Some OpenAI-compatible gateways do not implement /v1/models.
    Reporting a working deployment as broken would be its own failure."""
    def handler(request):
        return httpx.Response(404, text="nope")

    real = httpx.AsyncClient
    monkeypatch.setattr(
        preflight.httpx,
        "AsyncClient",
        lambda *a, **k: real(*a, **{**k, "transport": transport(handler)}),
    )

    check = await preflight.check_model(
        Settings(ai_api_key="k", ai_model="m", ai_base_url="http://x/v1")
    )

    assert check.status == "skipped"
    assert not check.failed


async def test_an_unconfigured_model_fails_rather_than_passing_quietly():
    check = await preflight.check_model(Settings(ai_api_key=None, ai_model=None))

    assert check.status == "fail"
    assert "AI_API_KEY and AI_MODEL" in check.detail


# --- the mock provider --------------------------------------------------


async def test_the_mock_provider_fails_in_production():
    checks = await preflight.check_whatsapp(
        Settings(app_env="production", whatsapp_provider="mock", app_url="https://a.example.com")
    )

    assert checks[0].status == "fail"


async def test_the_mock_provider_is_merely_noted_in_development():
    checks = await preflight.check_whatsapp(Settings(whatsapp_provider="mock"))

    assert checks[0].status == "skipped"
    assert not checks[0].failed


# --- secrets ------------------------------------------------------------


def test_a_placeholder_secret_fails_in_production():
    checks = preflight.check_secrets(
        Settings(
            app_env="production",
            app_url="https://a.example.com",
            jwt_secret="insecure-development-secret-change-me",
            webhook_secret="x" * 40,
            whatsapp_provider="mock",
        )
    )

    jwt = next(c for c in checks if c.name == "JWT_SECRET")
    assert jwt.status == "fail"


def test_a_real_whatsapp_provider_without_a_webhook_secret_fails():
    """Anyone who learned the webhook URL could otherwise post fake client
    messages into a firm's timeline."""
    checks = preflight.check_secrets(
        Settings(
            jwt_secret="x" * 40,
            webhook_secret="y" * 40,
            whatsapp_provider="whatsapp_cloud",
            whatsapp_webhook_secret=None,
        )
    )

    assert any(c.name == "WHATSAPP_WEBHOOK_SECRET" and c.status == "fail" for c in checks)


# --- output -------------------------------------------------------------


def test_no_credential_reaches_the_output(meta):
    """This gets pasted into chats and issue threads (§23)."""
    checks = [
        preflight.Check("A", "ok", "fine"),
        preflight.Check("B", "fail", "broken", remedy="do something"),
    ]

    rendered = preflight.render(checks, Settings(jwt_secret="s3cr3t-do-not-print"))

    assert "s3cr3t-do-not-print" not in rendered


def test_every_status_renders():
    """A KeyError in the renderer would hide the very report it was run for."""
    checks = [
        preflight.Check(name, status, "detail")
        for name, status in (("a", "ok"), ("b", "fail"), ("c", "warn"), ("d", "skipped"))
    ]

    rendered = preflight.render(checks, Settings())

    assert all(name in rendered for name in "abcd")
