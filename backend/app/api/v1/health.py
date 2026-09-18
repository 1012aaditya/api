"""Liveness and readiness (§31).

``/health`` answers "is this process up" and touches nothing.
``/ready`` answers "can this process serve traffic" and checks dependencies.
A missing AI provider is reported but does NOT make the process unready:
the rest of the API still works, and extraction requests fail loudly with
503 extraction_provider_unavailable rather than being silently faked.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_session_factory

router = APIRouter(tags=["health"])
logger = get_logger("docuparse.health")


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", summary="Readiness probe")
async def ready(response: Response) -> dict[str, object]:
    settings = get_settings()
    checks: dict[str, str] = {}

    try:
        async with get_session_factory()() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 — probe must not raise
        logger.warning("readiness.database_unavailable", error=type(exc).__name__)
        checks["database"] = "unavailable"

    checks["ai_provider"] = "configured" if settings.provider_configured else "not_configured"

    ready_now = checks["database"] == "ok"
    if not ready_now:
        response.status_code = 503
    return {"status": "ready" if ready_now else "not_ready", "checks": checks}
