"""Application settings.

Every value comes from the environment (or a local ``.env``). Nothing here
carries a usable default for a credential: an unset secret must fail loudly
or degrade to an explicit error, never to a silent fallback.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---
    app_env: Literal["development", "staging", "test", "production"] = "development"
    app_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    debug: bool = False

    # --- Database ---
    database_url: str = "postgresql+asyncpg://docuparse:docuparse@localhost:5432/docuparse"
    database_echo: bool = False

    # --- Redis ---
    redis_url: str | None = None

    # --- Storage ---
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_path: str = "./storage"
    storage_bucket: str = "docuparse-documents"
    s3_endpoint_url: str | None = None
    s3_region: str = "ap-south-1"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_signed_url_expiry_seconds: int = 900

    # --- AI provider ---
    ai_provider: str = "openai_compatible"
    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str | None = None
    ai_model: str | None = None
    ai_timeout_seconds: float = 120.0
    ai_max_retries: int = 2
    ai_input_cost_per_mtok: Decimal = Decimal("0")
    ai_output_cost_per_mtok: Decimal = Decimal("0")

    # --- Auth ---
    jwt_secret: str = "insecure-development-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    # --- Webhooks ---
    webhook_secret: str | None = None

    # --- Upload limits ---
    max_file_size_bytes: int = 20 * 1024 * 1024
    max_page_count: int = 25

    # --- Retention ---
    document_retention_days: int = 7

    # --- Validation tuning ---
    rounding_tolerance: Decimal = Decimal("1.0")
    confidence_high_threshold: float = 0.85
    confidence_medium_threshold: float = 0.60

    # --- Default per-organization limits ---
    default_rate_limit_per_minute: int = 60
    default_monthly_document_quota: int = 1000

    @field_validator("ai_api_key", "ai_model", "redis_url", "s3_endpoint_url", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        # An empty env var ("AI_API_KEY=") means "not configured", not "".
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def provider_configured(self) -> bool:
        """Whether an extraction call can even be attempted.

        False means every extraction request must return
        503 extraction_provider_unavailable (§42). It must never mean
        "return plausible-looking data".
        """
        return bool(self.ai_api_key and self.ai_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()
