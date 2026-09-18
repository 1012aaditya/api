"""Provider selection (§12, §42).

``AI_PROVIDER`` names the adapter. An unset or unknown provider is a
configuration error that surfaces as 503 — it is never quietly replaced by a
stub that returns invented data.
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.config import Settings, get_settings
from app.core.errors import ProviderUnavailableError
from app.core.logging import get_logger
from app.providers.base import DocumentAIProvider
from app.providers.openai_compatible import OpenAICompatibleProvider

logger = get_logger("docuparse.provider")

ProviderFactory = Callable[[Settings], DocumentAIProvider]

_FACTORIES: dict[str, ProviderFactory] = {
    "openai_compatible": OpenAICompatibleProvider,
    # Aliases: these endpoints all speak the same wire format.
    "openai": OpenAICompatibleProvider,
    "vllm": OpenAICompatibleProvider,
    "openrouter": OpenAICompatibleProvider,
    "together": OpenAICompatibleProvider,
    "litellm": OpenAICompatibleProvider,
    "ollama": OpenAICompatibleProvider,
}

_instance: DocumentAIProvider | None = None


def register_provider(name: str, factory: ProviderFactory) -> None:
    """Add an adapter. Used by future vendor-specific providers."""
    _FACTORIES[name] = factory


def build_provider(settings: Settings | None = None) -> DocumentAIProvider:
    settings = settings or get_settings()
    factory = _FACTORIES.get(settings.ai_provider)
    if factory is None:
        logger.error("provider.unknown", provider=settings.ai_provider)
        raise ProviderUnavailableError(
            f"AI_PROVIDER={settings.ai_provider!r} is not a provider this build knows about."
        )
    if not settings.provider_configured:
        raise ProviderUnavailableError(
            "No AI provider is configured. Set AI_API_KEY and AI_MODEL to enable extraction."
        )
    return factory(settings)


def get_provider() -> DocumentAIProvider:
    global _instance
    if _instance is None:
        _instance = build_provider()
    return _instance


def set_provider(provider: DocumentAIProvider | None) -> None:
    """Test seam. Production code never calls this."""
    global _instance
    _instance = provider


async def shutdown_provider() -> None:
    global _instance
    if _instance is not None:
        await _instance.aclose()
    _instance = None
