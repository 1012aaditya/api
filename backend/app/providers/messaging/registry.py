"""Choosing a messaging provider.

``WHATSAPP_PROVIDER`` names the adapter. There is no fallback to the mock when
credentials are missing: a deployment that believes it is messaging clients
while quietly sending nothing is worse than one that refuses to start (§42).
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.config import Settings, get_settings
from app.core.errors import DocuParseError
from app.core.logging import get_logger
from app.providers.messaging.base import WhatsAppProvider
from app.providers.messaging.mock import MockWhatsAppProvider

logger = get_logger("docuparse.whatsapp")

ProviderFactory = Callable[[Settings], WhatsAppProvider]


class MessagingUnavailableError(DocuParseError):
    code = "messaging_provider_unavailable"
    status_code = 503


_FACTORIES: dict[str, ProviderFactory] = {
    "mock": lambda _settings: MockWhatsAppProvider(),
}

_instance: WhatsAppProvider | None = None


def register_provider(name: str, factory: ProviderFactory) -> None:
    _FACTORIES[name] = factory


def build_provider(settings: Settings | None = None) -> WhatsAppProvider:
    settings = settings or get_settings()
    name = settings.whatsapp_provider
    factory = _FACTORIES.get(name)
    if factory is None:
        logger.error("whatsapp.unknown_provider", provider=name)
        raise MessagingUnavailableError(
            f"WHATSAPP_PROVIDER={name!r} is not a provider this build knows about."
        )
    return factory(settings)


def get_provider() -> WhatsAppProvider:
    global _instance
    if _instance is None:
        _instance = build_provider()
    return _instance


def set_provider(provider: WhatsAppProvider | None) -> None:
    """Test and demo seam."""
    global _instance
    _instance = provider


async def shutdown_provider() -> None:
    global _instance
    if _instance is not None:
        await _instance.aclose()
    _instance = None
