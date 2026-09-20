"""Per-organization request rate limiting (§18).

A fixed-window counter: cheap (one INCR), predictable, and adequate for
per-minute API limits. Redis backs it in any deployment with more than one
process; the in-memory backend exists so local development and the test
suite work without Redis, and it says so rather than pretending to be
cluster-correct.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from app.core.logging import get_logger

logger = get_logger("docuparse.ratelimit")


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int
    reset_at: int


class RateLimiter(Protocol):
    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision: ...


def _decide(count: int, limit: int, window_seconds: int, now: float) -> RateLimitDecision:
    window_start = int(now // window_seconds) * window_seconds
    reset_at = window_start + window_seconds
    allowed = count <= limit
    return RateLimitDecision(
        allowed=allowed,
        limit=limit,
        remaining=max(0, limit - count),
        retry_after_seconds=max(1, int(reset_at - now)),
        reset_at=reset_at,
    )


class InMemoryRateLimiter:
    """Single-process limiter. Correct for one worker, not across replicas."""

    def __init__(self) -> None:
        self._windows: dict[str, tuple[int, int]] = {}

    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        now = time.time()
        window_start = int(now // window_seconds) * window_seconds
        bucket, count = self._windows.get(key, (window_start, 0))
        if bucket != window_start:
            bucket, count = window_start, 0
        count += 1
        self._windows[key] = (bucket, count)
        if len(self._windows) > 10_000:
            self._evict(window_start)
        return _decide(count, limit, window_seconds, now)

    def _evict(self, current_window: int) -> None:
        for key, (bucket, _) in list(self._windows.items()):
            if bucket < current_window:
                del self._windows[key]

    def reset(self) -> None:
        self._windows.clear()


class RedisRateLimiter:
    """Shared limiter. Fails open if Redis is unreachable.

    Failing open is deliberate: an outage in the limiter should not take the
    API down with it. Quota enforcement (§18, documents/month) is a separate,
    database-backed check that does not fail open.
    """

    def __init__(self, redis_client: object) -> None:
        self._redis = redis_client

    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        now = time.time()
        window_start = int(now // window_seconds) * window_seconds
        redis_key = f"docuparse:ratelimit:{key}:{window_start}"
        try:
            pipeline = self._redis.pipeline()
            pipeline.incr(redis_key)
            pipeline.expire(redis_key, window_seconds + 1)
            count = (await pipeline.execute())[0]
        except Exception as exc:  # noqa: BLE001 — availability over strictness
            logger.warning("ratelimit.backend_unavailable", error=type(exc).__name__)
            return RateLimitDecision(
                allowed=True,
                limit=limit,
                remaining=limit,
                retry_after_seconds=0,
                reset_at=window_start + window_seconds,
            )
        return _decide(int(count), limit, window_seconds, now)


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = build_rate_limiter()
    return _limiter


class RateLimiterMisconfigured(RuntimeError):
    """Raised at boot rather than serving traffic with a limit that is not one."""


def build_rate_limiter() -> RateLimiter:
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.redis_url:
        if settings.is_production and not settings.allow_in_memory_rate_limit:
            # The in-memory counter lives inside one process. Run four API
            # workers and every organization silently gets four times its
            # limit, which is not a rate limit — it is a number in a log
            # line. Refusing to boot is the honest outcome (§42).
            raise RateLimiterMisconfigured(
                "APP_ENV=production needs REDIS_URL: the in-memory rate "
                "limiter counts per process, so N API workers would grant "
                "every organization N times its limit. Set REDIS_URL, or "
                "ALLOW_IN_MEMORY_RATE_LIMIT=true if you really do run a "
                "single worker."
            )
        if settings.is_production:
            logger.warning("ratelimit.in_memory_in_production")
        return InMemoryRateLimiter()
    try:
        import redis.asyncio as redis

        return RedisRateLimiter(redis.from_url(settings.redis_url, decode_responses=True))
    except Exception as exc:  # noqa: BLE001
        if settings.is_production and not settings.allow_in_memory_rate_limit:
            raise RateLimiterMisconfigured(
                f"REDIS_URL is set but the client would not start ({type(exc).__name__}). "
                "Falling back to the per-process counter in production would "
                "quietly multiply every limit by the worker count."
            ) from exc
        logger.warning("ratelimit.redis_init_failed", error=type(exc).__name__)
        return InMemoryRateLimiter()


def set_rate_limiter(limiter: RateLimiter | None) -> None:
    """Test seam."""
    global _limiter
    _limiter = limiter
