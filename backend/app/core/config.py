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
    #: Where the **dashboard** is served, not the API. It is the base for
    #: invitation links and the default CORS origin, both of which a browser
    #: has to reach. In production this is your app domain.
    app_url: str = "http://localhost:3000"
    #: Browser origins allowed to call this API, comma separated. Defaults to
    #: ``app_url`` alone. Set it when the dashboard is served from more than
    #: one hostname. Never "*" in production — see ``cors_origins``.
    cors_allow_origins: str | None = None
    log_level: str = "INFO"
    debug: bool = False

    # --- Database ---
    database_url: str = "postgresql+asyncpg://docuparse:docuparse@localhost:5432/docuparse"
    database_echo: bool = False
    #: Connections held open per process. Every API worker and every job
    #: worker keeps its own pool, so the ceiling Postgres sees is
    #: (api_workers + job_workers) x (pool_size + max_overflow). Exceed
    #: ``max_connections`` and requests fail with "too many clients" under
    #: exactly the load you sized the workers for. DEPLOYMENT.md does the
    #: arithmetic.
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # --- Redis ---
    #: Required in production: rate limiting is per-process without it, so
    #: N API workers would grant every organization N times its limit. Set
    #: ``allow_in_memory_rate_limit`` to run without it anyway.
    redis_url: str | None = None
    allow_in_memory_rate_limit: bool = False

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

    # --- Webhooks (§22) ---
    # Per-endpoint signing secrets are derived from this, so no webhook secret
    # is ever stored. Unset means webhooks cannot be created.
    webhook_secret: str | None = None
    webhook_max_attempts: int = 5
    webhook_timeout_seconds: float = 10.0
    # Consecutive failures before an endpoint is disabled and we stop calling it.
    webhook_failure_threshold: int = 20
    # Customer-supplied URLs are an SSRF vector. Off in production, always.
    webhook_allow_private_urls: bool = False
    webhook_require_https: bool = True

    # --- Async jobs (§6) ---
    job_max_attempts: int = 3
    worker_poll_interval_seconds: float = 2.0
    worker_batch_size: int = 5
    # A job claimed but never finished (worker killed mid-run) is returned to
    # the queue after this long.
    job_stale_after_seconds: int = 900

    # --- Client messaging (§7) ---
    #: "mock" records messages without sending them — what demo mode and the
    #: tests use. A real adapter is named here once one exists. There is no
    #: automatic fallback: silently sending nothing is worse than refusing.
    whatsapp_provider: str = "mock"
    whatsapp_verify_token: str | None = None
    #: Shared secret for verifying inbound webhook signatures.
    whatsapp_webhook_secret: str | None = None
    voice_provider: str = "mock"

    # --- Exotel (VOICE_PROVIDER=exotel) ---
    #: The account SID, from the Exotel dashboard.
    exotel_sid: str | None = None
    exotel_api_key: str | None = None
    exotel_api_token: str | None = None
    #: The ExoPhone the client sees as the caller. Not the CA's mobile.
    exotel_caller_id: str | None = None
    #: The App (flow) that speaks when the client picks up. Exotel plays a
    #: flow you build in their dashboard — this code cannot put words in that
    #: call, so the flow itself has to open by saying it is an AI assistant
    #: calling on the firm's behalf. See the README.
    exotel_flow_id: str | None = None
    #: "in" for the Mumbai cluster, "sg" for Singapore.
    exotel_region: str = "in"

    # --- WhatsApp Cloud API (WHATSAPP_PROVIDER=whatsapp_cloud) ---
    #: The sending number's id in the WhatsApp Business account, not the
    #: number itself.
    whatsapp_phone_number_id: str | None = None
    #: A permanent system-user token. Never logged, never returned by the API.
    whatsapp_access_token: str | None = None
    whatsapp_api_base: str = "https://graph.facebook.com"
    whatsapp_api_version: str = "v21.0"
    #: Meta allows free-form text only within 24 hours of the client's last
    #: message. Outside it, the first contact has to be an approved template.
    whatsapp_template_name: str | None = None
    whatsapp_template_language: str = "en"

    # --- Upload limits ---
    max_file_size_bytes: int = 20 * 1024 * 1024
    max_page_count: int = 25
    # Files accepted in one bulk upload. A cap, because the whole batch
    # arrives as a single multipart body held in memory.
    max_batch_files: int = 200

    # --- Retention ---
    document_retention_days: int = 7

    # --- Extraction tiers (§34) ---
    # Ordered, cheapest first. Dropping "model" makes the deployment fully
    # local and model-free: documents that the cheap tiers cannot read come
    # back partially filled with the gaps reported, rather than guessed.
    extraction_tiers: str = "qr,text_layer,model"
    # Line items are never produced by the qr or text_layer tiers. Leave this
    # on and a document with a line-item table escalates to the model; turn it
    # off when header-level data is all your workflow needs.
    extraction_require_line_items: bool = True

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
    def cors_origins(self) -> list[str]:
        """Which browser origins may call this API.

        Development allows anything, because the dashboard moves between
        ports and a CORS failure looks like a network error with no status.
        Production allows exactly what is listed — a wildcard there would let
        any page on the internet drive a signed-in CA's session.
        """
        if self.cors_allow_origins:
            listed = [o.strip().rstrip("/") for o in self.cors_allow_origins.split(",")]
            origins = [origin for origin in listed if origin]
            if origins:
                return origins
        if not self.is_production:
            return ["*"]
        return [self.app_url.rstrip("/")]

    @property
    def enabled_tiers(self) -> tuple[str, ...]:
        seen: list[str] = []
        for name in self.extraction_tiers.split(","):
            cleaned = name.strip().lower()
            if cleaned and cleaned not in seen:
                seen.append(cleaned)
        return tuple(seen)

    @property
    def model_tier_enabled(self) -> bool:
        return "model" in self.enabled_tiers

    @property
    def webhooks_configured(self) -> bool:
        """Whether webhook endpoints can be created and signed.

        Without a master secret there is nothing to derive signing keys from,
        and an unsigned webhook is worse than none — the receiver has no way
        to tell our call from anyone else's.
        """
        return bool(self.webhook_secret)

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
