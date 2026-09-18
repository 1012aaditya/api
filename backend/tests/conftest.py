"""Test configuration.

The suite runs with no external services: SQLite on a temp file stands in for
PostgreSQL, the object store is in-memory, and the AI provider is a stub.

The stub provider lives here, in the tests, and is reachable only by
explicitly injecting it. There is no code path in ``app/`` that can fall back
to it — a deployment without credentials returns 503, it does not quietly
serve fixture data (§42).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

_TMP_DIR = tempfile.mkdtemp(prefix="docuparse-tests-")
_DB_PATH = Path(_TMP_DIR) / "test.db"

# Settings are read at import time, so the environment must be set first.
os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": f"sqlite+aiosqlite:///{_DB_PATH}",
        "REDIS_URL": "",
        "STORAGE_BACKEND": "local",
        "STORAGE_LOCAL_PATH": str(Path(_TMP_DIR) / "storage"),
        "JWT_SECRET": "test-secret-not-used-anywhere-real",
        "AI_PROVIDER": "openai_compatible",
        "AI_API_KEY": "",
        "AI_MODEL": "",
        "LOG_LEVEL": "WARNING",
        "MAX_FILE_SIZE_BYTES": str(5 * 1024 * 1024),
        "MAX_PAGE_COUNT": "10",
        "DEFAULT_RATE_LIMIT_PER_MINUTE": "60",
        "DEFAULT_MONTHLY_DOCUMENT_QUOTA": "100",
        "DOCUMENT_RETENTION_DAYS": "7",
        "ROUNDING_TOLERANCE": "1.0",
    }
)

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.rate_limit import InMemoryRateLimiter, set_rate_limiter  # noqa: E402
from app.core.security import generate_api_key, hash_password  # noqa: E402
from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402
from app.pipelines.stages.preprocess import PreparedDocument, PreparedPage  # noqa: E402
from app.providers.base import (  # noqa: E402
    DocumentAIProvider,
    ProviderResult,
    ProviderUsage,
    StructuredResult,
)
from app.providers.registry import set_provider  # noqa: E402
from app.repositories.api_keys import APIKeyRepository  # noqa: E402
from app.repositories.organizations import OrganizationRepository  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.services.storage import ObjectStore, set_object_store  # noqa: E402
from tests.fixtures.invoices import SAMPLE_PROVIDER_OUTPUT  # noqa: E402

# --- Test doubles ------------------------------------------------------


class InMemoryObjectStore(ObjectStore):
    name = "local"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        self.objects[key] = data

    async def get(self, key: str) -> bytes:
        return self.objects[key]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def signed_url(self, key: str, *, expires_in: int = 900) -> str | None:
        return None


class StubProvider(DocumentAIProvider):
    """Returns a scripted payload. Never used outside tests."""

    name = "stub"

    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        raises: Exception | None = None,
        usage: ProviderUsage | None = None,
    ) -> None:
        self.payload = payload if payload is not None else SAMPLE_PROVIDER_OUTPUT
        self.raises = raises
        self.usage = usage or ProviderUsage(input_tokens=1200, output_tokens=400)
        self.calls: list[dict[str, Any]] = []

    @property
    def model(self) -> str:
        return "stub-model-v1"

    async def extract_text(self, document: PreparedDocument) -> ProviderResult:
        return ProviderResult(text="", usage=self.usage, model=self.model, latency_ms=1)

    async def analyze_image(self, page: PreparedPage, *, prompt: str) -> ProviderResult:
        return ProviderResult(text="", usage=self.usage, model=self.model, latency_ms=1)

    async def extract_structured_data(
        self,
        document: PreparedDocument,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any] | None = None,
    ) -> StructuredResult:
        self.calls.append({"pages": document.page_count, "system_prompt": system_prompt})
        if self.raises is not None:
            raise self.raises
        return StructuredResult(
            data=self.payload, usage=self.usage, model=self.model, latency_ms=42
        )


# --- Fixtures ----------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> Iterator[None]:
    """Build the schema with the real migration, not metadata.create_all.

    This is the only place the migration runs end to end, so a migration that
    drifts from the models fails the suite rather than production.
    """
    from alembic.config import Config

    from alembic import command

    config = Config("alembic.ini")
    config.set_main_option("script_location", "alembic")
    command.upgrade(config, "head")
    yield


@pytest.fixture(autouse=True)
async def _clean_database() -> AsyncIterator[None]:
    yield
    async with get_session_factory()() as session:
        for table in reversed(Base.metadata.sorted_tables):
            await session.execute(table.delete())
        await session.commit()


@pytest.fixture(autouse=True)
def _reset_singletons() -> Iterator[InMemoryObjectStore]:
    store = InMemoryObjectStore()
    set_object_store(store)
    set_rate_limiter(InMemoryRateLimiter())
    yield store
    set_object_store(None)
    set_rate_limiter(None)


@pytest.fixture
def object_store(_reset_singletons: InMemoryObjectStore) -> InMemoryObjectStore:
    return _reset_singletons


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:
    # raise_app_exceptions=False mirrors a real server: Starlette sends the
    # 500 response and then re-raises so the server can log it. Letting the
    # re-raise escape into the test would hide the response we want to assert
    # on.
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as http_client:
        yield http_client


@pytest.fixture(scope="session", autouse=True)
async def _dispose_engine_at_end() -> AsyncIterator[None]:
    yield
    await dispose_engine()




@dataclass
class Tenant:
    """One organization, with a user session and a usable API key."""

    organization_id: str
    user_id: str
    email: str
    password: str
    api_key: str
    api_key_id: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}


async def create_tenant(
    name: str = "Acme Accounting",
    *,
    email: str | None = None,
    environment: str = "live",
) -> Tenant:
    email = email or f"{uuid4().hex[:10]}@example.com"
    password = "correct-horse-battery-staple"
    async with get_session_factory()() as session:
        organization = await OrganizationRepository(session).create(name=name)
        user = await UserRepository(session).create(
            organization_id=organization.id,
            email=email,
            password_hash=hash_password(password),
        )
        generated = generate_api_key(environment)  # type: ignore[arg-type]
        api_key = await APIKeyRepository(session).create(
            organization_id=organization.id,
            key_hash=generated.key_hash,
            prefix=generated.prefix,
            last_four=generated.last_four,
            name="Test key",
            environment=environment,
            created_by_user_id=user.id,
        )
        await session.commit()
        return Tenant(
            organization_id=organization.id,
            user_id=user.id,
            email=email,
            password=password,
            api_key=generated.plaintext,
            api_key_id=api_key.id,
        )


@pytest.fixture
async def tenant() -> Tenant:
    return await create_tenant()


@pytest.fixture
async def other_tenant() -> Tenant:
    return await create_tenant("Rival Books Pvt Ltd")


@pytest.fixture
async def session_token(client: httpx.AsyncClient, tenant: Tenant) -> str:
    response = await client.post(
        "/v1/auth/login", json={"email": tenant.email, "password": tenant.password}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["access_token"]


@pytest.fixture
def auth_headers(session_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {session_token}"}


@pytest.fixture
def stub_provider() -> StubProvider:
    return StubProvider()


@pytest.fixture
def use_provider(app):
    """Install a stub provider for the duration of one test."""

    def _install(provider: DocumentAIProvider) -> None:
        set_provider(provider)

    yield _install
    set_provider(None)
