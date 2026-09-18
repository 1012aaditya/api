"""The background worker (§6, §22).

One loop, three jobs, in this order:

1. return jobs a dead worker abandoned to the queue,
2. process queued extractions,
3. push any webhook deliveries that are due.

Everything it does is idempotent at the row level, so several workers can run
at once and a worker that is killed mid-job loses at most that job's attempt.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import signal

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.base import utcnow
from app.db.session import dispose_engine, get_session_factory
from app.repositories.jobs import JobRepository
from app.services.extraction_service import ExtractionService
from app.services.webhooks import deliver_due

logger = get_logger("docuparse.worker")


async def release_stale_jobs(settings: Settings) -> int:
    """Requeue jobs stuck in ``processing`` because their worker died."""
    cutoff = utcnow() - dt.timedelta(seconds=settings.job_stale_after_seconds)
    async with get_session_factory()() as session:
        released = await JobRepository(session).release_stale(older_than=cutoff)
        await session.commit()
    if released:
        logger.warning("worker.released_stale_jobs", count=released)
    return released


async def process_available_jobs(settings: Settings, *, limit: int) -> int:
    """Claim and run up to ``limit`` jobs. Returns how many were attempted."""
    attempted = 0
    for _ in range(limit):
        async with get_session_factory()() as session:
            job = await JobRepository(session).claim_next()
            if job is None:
                await session.rollback()
                break
            # The claim is committed on its own so the job is visibly
            # "processing" even if this worker dies during the run.
            await session.commit()
            attempted += 1
            await ExtractionService(session).process_job(job, settings=settings)
    return attempted


async def flush_webhooks(settings: Settings, *, client: httpx.AsyncClient) -> dict[str, int]:
    async with get_session_factory()() as session:
        return await deliver_due(session, settings=settings, client=client, limit=20)


async def run_once(
    settings: Settings | None = None, *, client: httpx.AsyncClient | None = None
) -> dict[str, int]:
    """A single pass. Exposed so tests can drive the worker deterministically."""
    settings = settings or get_settings()
    released = await release_stale_jobs(settings)
    jobs = await process_available_jobs(settings, limit=settings.worker_batch_size)

    owned = client is None
    http = client or httpx.AsyncClient()
    try:
        deliveries = await flush_webhooks(settings, client=http)
    finally:
        if owned:
            await http.aclose()

    return {"released": released, "jobs": jobs, **deliveries}


async def run_forever(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    stopping = asyncio.Event()

    def request_stop() -> None:
        logger.info("worker.stop_requested")
        stopping.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, request_stop)

    logger.info(
        "worker.started",
        poll_interval_seconds=settings.worker_poll_interval_seconds,
        batch_size=settings.worker_batch_size,
        provider_configured=settings.provider_configured,
        webhooks_configured=settings.webhooks_configured,
    )

    async with httpx.AsyncClient() as client:
        while not stopping.is_set():
            try:
                tally = await run_once(settings, client=client)
                if tally["jobs"] or tally["delivered"] or tally["failed"]:
                    logger.info("worker.pass_completed", **tally)
                # Work found means more may be waiting; poll again immediately.
                idle = tally["jobs"] == 0 and tally["attempted"] == 0
            except Exception:  # noqa: BLE001 — one bad pass must not end the worker
                logger.exception("worker.pass_failed")
                idle = True

            if idle:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        stopping.wait(), timeout=settings.worker_poll_interval_seconds
                    )

    logger.info("worker.stopped")


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.app_env != "development")
    try:
        asyncio.run(_run_and_dispose(settings))
    except KeyboardInterrupt:
        pass
    return 0


async def _run_and_dispose(settings: Settings) -> None:
    try:
        await run_forever(settings)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(main())
