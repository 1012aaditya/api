"""DocuParse API application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import Settings, get_settings
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.db.session import dispose_engine

DESCRIPTION = """
Turn Indian GST invoices into structured, validated JSON.

Authenticate with an API key: `Authorization: Bearer dp_live_...`.
Every response carries a `request_id` — quote it in support requests.
"""


def _build_v1_router() -> APIRouter:
    from app.api.v1 import api_keys, auth, documents, invoices, usage

    router = APIRouter(prefix="/v1")
    router.include_router(auth.router)
    router.include_router(api_keys.router)
    router.include_router(invoices.router)
    router.include_router(documents.router)
    router.include_router(usage.router)
    return router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    logger = get_logger("docuparse.startup")
    logger.info(
        "application.started",
        env=settings.app_env,
        ai_provider=settings.ai_provider,
        provider_configured=settings.provider_configured,
        storage_backend=settings.storage_backend,
        rate_limit_backend="redis" if settings.redis_url else "in-memory",
    )
    if not settings.provider_configured:
        # Loud, once, at boot. Extraction requests will return 503 rather
        # than invent data (§42).
        logger.warning("application.ai_provider_not_configured")
    try:
        yield
    finally:
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.app_env != "development")

    app = FastAPI(
        title="DocuParse API",
        description=DESCRIPTION,
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if not settings.is_production else [settings.app_url],
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
        expose_headers=["X-Request-Id", "Retry-After"],
    )

    register_exception_handlers(app)

    from app.api.v1 import health

    app.include_router(health.router)
    app.include_router(_build_v1_router())
    return app


app = create_app()
