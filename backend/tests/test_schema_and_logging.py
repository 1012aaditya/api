"""The database schema, the log contract, and the OpenAPI surface."""

from __future__ import annotations

import httpx

from app.core.logging import _REDACTED_KEYS, _redact


async def test_the_migration_matches_the_models() -> None:
    """A model change without a migration fails here, not in production.

    Runs through the application's own async engine and ``run_sync`` rather
    than rebuilding a sync URL by hand. The old version stripped "+aiosqlite"
    off the URL, which silently did nothing to a "+asyncpg" one and handed the
    async driver to a synchronous engine — so this check could only ever run
    on SQLite, which is exactly the dialect it is least useful on.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.db.session import get_engine
    from app.models import Base

    def compare(connection) -> list:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        return compare_metadata(context, Base.metadata)

    async with get_engine().connect() as connection:
        diff = await connection.run_sync(compare)

    assert diff == [], f"models and migrations have drifted: {diff}"


def test_secrets_are_redacted_from_log_records() -> None:
    record = {
        "event": "test",
        "api_key": "dp_live_secret",
        "authorization": "Bearer dp_live_secret",
        "password": "hunter2",
        "ai_api_key": "sk-abc",
        "key_hash": "deadbeef",
        "invoice_data": {"total": 118000},
        "endpoint": "/v1/invoices/extract",
        "status": 200,
    }
    redacted = _redact(None, "info", dict(record))
    for key in _REDACTED_KEYS & set(record):
        assert redacted[key] == "[redacted]"
    # Non-sensitive observability fields survive.
    assert redacted["endpoint"] == "/v1/invoices/extract"
    assert redacted["status"] == 200


def test_the_redaction_list_covers_the_obvious_secrets() -> None:
    for key in ("api_key", "authorization", "password", "secret", "token", "ai_api_key"):
        assert key in _REDACTED_KEYS


async def test_the_openapi_document_builds_and_covers_every_endpoint(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    for expected in (
        "/health",
        "/ready",
        "/v1/auth/signup",
        "/v1/auth/login",
        "/v1/api-keys",
        "/v1/invoices/extract",
        "/v1/documents",
    ):
        assert expected in paths


async def test_the_extraction_endpoint_documents_its_error_codes(
    client: httpx.AsyncClient,
) -> None:
    spec = (await client.get("/openapi.json")).json()
    responses = spec["paths"]["/v1/invoices/extract"]["post"]["responses"]
    for status_code in ("400", "401", "403", "413", "415", "422", "429", "503"):
        assert status_code in responses


def test_supported_document_types_are_only_what_actually_works() -> None:
    """§35 leaves room for more types; §42 forbids advertising ones that do not work."""
    from app.pipelines.strategies import get_strategy, supported_document_types

    assert supported_document_types() == ["gst_invoice"]
    assert get_strategy("gst_invoice").document_type == "gst_invoice"


def test_requesting_an_unregistered_document_type_is_refused() -> None:
    import pytest

    from app.core.errors import InvalidRequestError
    from app.pipelines.strategies import get_strategy

    with pytest.raises(InvalidRequestError):
        get_strategy("bank_statement")


def test_no_log_call_uses_the_reserved_event_key() -> None:
    """``event`` is structlog's own positional parameter.

    Passing it as a context kwarg raises TypeError at the call site — which
    means the bug only shows up when that particular line runs. This catches
    it at import time instead.
    """
    import ast
    import pathlib

    from app.core.logging import RESERVED_LOG_KEYS

    offenders: list[str] = []
    for path in pathlib.Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"debug", "info", "warning", "error", "exception"}:
                continue
            target = node.func.value
            if not (isinstance(target, ast.Name) and target.id == "logger"):
                continue
            for keyword in node.keywords:
                if keyword.arg in RESERVED_LOG_KEYS:
                    offenders.append(f"{path}:{node.lineno} passes {keyword.arg}=")
    assert offenders == [], offenders
