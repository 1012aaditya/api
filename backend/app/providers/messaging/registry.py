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


def _cloud(settings: Settings) -> WhatsAppProvider:
    """Build the Cloud API adapter, or refuse to.

    Missing credentials are a configuration error, not a reason to fall back
    to something that sends nothing (§42).
    """
    from app.providers.messaging.whatsapp_cloud import WhatsAppCloudProvider

    missing = [
        name
        for name, value in (
            ("WHATSAPP_PHONE_NUMBER_ID", settings.whatsapp_phone_number_id),
            ("WHATSAPP_ACCESS_TOKEN", settings.whatsapp_access_token),
        )
        if not value
    ]
    if missing:
        raise MessagingUnavailableError(
            f"WHATSAPP_PROVIDER=whatsapp_cloud needs {' and '.join(missing)}."
        )
    return WhatsAppCloudProvider(settings)


_FACTORIES: dict[str, ProviderFactory] = {
    "mock": lambda _settings: MockWhatsAppProvider(),
    "whatsapp_cloud": _cloud,
}

_instance: WhatsAppProvider | None = None


def register_provider(name: str, factory: ProviderFactory) -> None:
    _FACTORIES[name] = factory


def build_provider(settings: Settings | None = None) -> WhatsAppProvider:
    settings = settings or get_settings()
    name = settings.whatsapp_provider
    if name == "mock" and settings.is_production:
        # The mock reports every message as sent. In production that is a
        # lie the firm would act on — the dashboard would show "WhatsApp
        # sent to Marigold Retail" for a message no phone ever received
        # (§28, §42). Better to refuse to start.
        logger.error("whatsapp.mock_in_production")
        raise MessagingUnavailableError(
            "WHATSAPP_PROVIDER=mock sends nothing and reports success, which is "
            "not something a production deployment may do. Configure a real "
            "provider, or leave WhatsApp switched off in the agent policy."
        )
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
