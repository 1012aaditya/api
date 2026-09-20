"""What a production deployment is not allowed to get wrong.

Each of these is a mistake that looks like nothing in development and shows
up as a security hole or an outage under load. They are refusals at boot
rather than warnings in a log, because nobody reads a warning in a log on
the day they deploy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.rate_limit import (
    InMemoryRateLimiter,
    RateLimiterMisconfigured,
    RedisRateLimiter,
    build_rate_limiter,
    set_rate_limiter,
)


def settings_for(monkeypatch, **values) -> Settings:
    """A Settings built from an explicit environment.

    The real one is an lru_cache, so the fixture resets it afterwards rather
    than leaking a production-shaped configuration into the next test.
    """
    from app.core import config as config_module

    built = Settings(**values)
    monkeypatch.setattr(config_module, "get_settings", lambda: built)
    return built


def _production_template() -> dict[str, str]:
    """The committed .env.production.example, parsed.

    Read from disk rather than duplicated here: a copy would agree with
    itself forever while the file people actually use drifted away.
    """
    path = Path(__file__).resolve().parents[2] / ".env.production.example"
    assert path.exists(), f"{path} is missing"

    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        values[name.strip()] = value.strip()
    return values


@pytest.fixture(autouse=True)
def _restore_limiter():
    yield
    set_rate_limiter(None)


# --- the rate limiter has to actually limit -----------------------------


def test_production_without_redis_refuses_to_start(monkeypatch):
    """Four API workers each counting to sixty is not a limit of sixty."""
    settings_for(monkeypatch, app_env="production", redis_url=None, app_url="https://app.example.com")

    with pytest.raises(RateLimiterMisconfigured) as raised:
        build_rate_limiter()

    assert "REDIS_URL" in str(raised.value)


def test_a_single_worker_deployment_may_opt_out(monkeypatch):
    """The escape hatch is explicit, so it cannot happen by accident."""
    settings_for(
        monkeypatch,
        app_env="production",
        redis_url=None,
        allow_in_memory_rate_limit=True,
        app_url="https://app.example.com",
    )

    assert isinstance(build_rate_limiter(), InMemoryRateLimiter)


def test_development_needs_no_redis(monkeypatch):
    settings_for(monkeypatch, app_env="development", redis_url=None)

    assert isinstance(build_rate_limiter(), InMemoryRateLimiter)


def test_a_redis_url_is_used_when_given(monkeypatch):
    settings_for(
        monkeypatch,
        app_env="production",
        redis_url="redis://localhost:6379/0",
        app_url="https://app.example.com",
    )

    assert isinstance(build_rate_limiter(), RedisRateLimiter)


def test_an_unreachable_redis_is_not_quietly_swapped_for_no_limit(monkeypatch):
    """Falling back here would multiply every limit by the worker count,
    silently, at exactly the moment Redis was having a bad day."""
    settings_for(
        monkeypatch,
        app_env="production",
        redis_url="redis://localhost:6379/0",
        app_url="https://app.example.com",
    )
    monkeypatch.setattr(
        "redis.asyncio.from_url",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no route to host")),
    )

    with pytest.raises(RateLimiterMisconfigured):
        build_rate_limiter()


# --- CORS ---------------------------------------------------------------


def test_development_allows_any_origin():
    """The dashboard moves between ports locally, and a CORS failure looks
    like a network error with no status."""
    assert Settings(app_env="development").cors_origins == ["*"]


def test_production_allows_only_the_dashboard():
    settings = Settings(app_env="production", app_url="https://app.example.com")

    assert settings.cors_origins == ["https://app.example.com"]
    assert "*" not in settings.cors_origins


def test_production_never_widens_to_a_wildcard():
    """Any page on the internet could otherwise drive a signed-in CA's
    session."""
    settings = Settings(app_env="production", app_url="https://app.example.com")

    assert settings.cors_origins != ["*"]


def test_several_dashboard_hostnames_are_allowed_when_listed():
    settings = Settings(
        app_env="production",
        app_url="https://app.example.com",
        cors_allow_origins="https://app.example.com, https://www.example.com/",
    )

    assert settings.cors_origins == [
        "https://app.example.com",
        "https://www.example.com",
    ]


def test_a_trailing_slash_does_not_silently_break_cors():
    """Browsers send an Origin with no trailing slash, so one here would
    never match and every request would be blocked."""
    settings = Settings(app_env="production", app_url="https://app.example.com/")

    assert settings.cors_origins == ["https://app.example.com"]


# --- the connection ceiling ---------------------------------------------


def test_the_pool_is_tunable_rather_than_hardcoded():
    """(API workers + job workers) x (pool + overflow) has to stay under
    Postgres's max_connections, which means it has to be settable."""
    settings = Settings(db_pool_size=5, db_max_overflow=5)

    assert settings.db_pool_size == 5
    assert settings.db_max_overflow == 5


def test_the_production_template_stays_under_its_own_connection_limit():
    """DEPLOYMENT.md does this sum for the reader and tells them to redo it
    whenever they scale the workers. If the template drifts out of step with
    its own POSTGRES_MAX_CONNECTIONS, the guide is walking people into
    "too many clients" on filing day — so the arithmetic is checked here
    rather than trusted."""
    template = _production_template()

    ceiling = (
        int(template["API_WORKERS"]) + int(template["WORKER_REPLICAS"])
    ) * (int(template["DB_POOL_SIZE"]) + int(template["DB_MAX_OVERFLOW"]))

    assert ceiling < int(template["POSTGRES_MAX_CONNECTIONS"]), (
        f"the template's own workers can open {ceiling} connections against "
        f"a limit of {template['POSTGRES_MAX_CONNECTIONS']}"
    )


def test_the_production_template_refuses_the_mock_providers():
    """WHATSAPP_PROVIDER=mock is refused at boot under APP_ENV=production.
    A template that shipped it would hand someone a stack that will not
    start, on the day they are trying to go live."""
    template = _production_template()

    assert template["WHATSAPP_PROVIDER"] != "mock"
    assert template.get("VOICE_PROVIDER") != "mock"


def test_the_production_template_carries_no_secret():
    """It is committed to the repository. Every credential in it must be
    blank, so there is nothing to leak and nothing to forget to change."""
    template = _production_template()

    for name in (
        "JWT_SECRET",
        "WEBHOOK_SECRET",
        "POSTGRES_PASSWORD",
        "WHATSAPP_ACCESS_TOKEN",
        "WHATSAPP_WEBHOOK_SECRET",
        "EXOTEL_API_TOKEN",
        "S3_SECRET_ACCESS_KEY",
    ):
        assert not template.get(name), f"{name} has a value in a committed file"


def test_every_row_of_the_sizing_table_adds_up():
    """DEPLOYMENT.md hands the reader a table of worker counts and the
    max_connections each needs. A row whose arithmetic is wrong is worse
    than no table: someone scales to it and finds out on filing day."""
    import re

    guide = (Path(__file__).resolve().parents[2] / "DEPLOYMENT.md").read_text()
    pool = 10 + 10  # the values the production template ships

    rows = re.findall(
        r"^\|[^|]*\|\s*\**(\d+)\**[^|]*\|\s*\**(\d+)\**\s*\|"
        r"\s*\**(\d+)\**\s*\|\s*\**(\d+)\**[^|]*\|\s*$",
        guide,
        re.MULTILINE,
    )
    assert len(rows) >= 3, f"the sizing table was not found or changed shape ({len(rows)} rows)"

    for api_workers, job_workers, stated, max_connections in rows:
        ceiling = (int(api_workers) + int(job_workers)) * pool
        assert ceiling == int(stated), (
            f"{api_workers} API + {job_workers} job workers is {ceiling} "
            f"connections, but the table says {stated}"
        )
        assert ceiling < int(max_connections), (
            f"{ceiling} connections against a stated max_connections of {max_connections}"
        )
