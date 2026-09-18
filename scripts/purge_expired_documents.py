#!/usr/bin/env python
"""Delete stored documents past their retention window (§24).

Run on a schedule, e.g. hourly:

    0 * * * * cd /srv/docuparse/backend && .venv/bin/python \
        ../scripts/purge_expired_documents.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.db.session import dispose_engine  # noqa: E402
from app.services.retention import purge_all_expired  # noqa: E402


async def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger("docuparse.retention.cli")
    try:
        purged = await purge_all_expired()
    finally:
        await dispose_engine()
    logger.info("retention.run_completed", purged=purged)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
