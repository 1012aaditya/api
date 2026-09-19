"""Whether this firm's documents may be shown to this model (§19).

The Agent page has a switch reading "keep documents on your own hardware".
Until this file existed the switch was stored and displayed and checked
nowhere, which is the worst state for a privacy control to be in: the firm
believes something about where their clients' papers go, and the belief is
wrong.

The rule is deliberately blunt. A model endpoint is either on this machine or
this network, or it is somewhere else. There is no "probably fine" — a
hostname that cannot be shown to be local is treated as remote.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import DocuParseError
from app.core.logging import get_logger
from app.repositories.operations import AgentPolicyRepository

logger = get_logger("docuparse.ai_policy")

#: Names that mean "this machine" whatever the network does.
_LOCAL_NAMES = frozenset(
    {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}
)

#: Hostnames a self-hosted model is reached by inside a private network.
_LOCAL_SUFFIXES = (".local", ".internal", ".lan", ".localdomain")


class ModelNotLocalError(DocuParseError):
    code = "model_not_local"
    status_code = 503


def is_local_endpoint(base_url: str | None) -> bool:
    """Whether this URL can be reached without leaving the premises."""
    if not base_url:
        # No endpoint configured is not an endpoint that leaks anything.
        return True

    host = (urlparse(base_url).hostname or "").strip().lower()
    if not host:
        return False
    if host in _LOCAL_NAMES or host.endswith(_LOCAL_SUFFIXES):
        return True

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A public DNS name. It may resolve to a private address, but this is
        # a promise to a firm about where their clients' invoices go, and
        # "might be fine" is not a promise anyone should make.
        return False
    return address.is_private or address.is_loopback or address.is_link_local


async def model_allowed(
    db: AsyncSession, organization_id: str, *, settings: Settings | None = None
) -> tuple[bool, str | None]:
    """May this firm's data go to the configured model? With the reason."""
    settings = settings or get_settings()
    policy = await AgentPolicyRepository(db).get_or_create(organization_id)
    if not policy.local_ai_only:
        return True, None
    if is_local_endpoint(settings.ai_base_url):
        return True, None

    reason = (
        "This firm is set to keep documents on its own hardware, and the "
        f"configured model at {_safe_host(settings.ai_base_url)} is not local. "
        "Point AI_BASE_URL at a model running on your own machine or network, "
        "or turn the setting off."
    )
    logger.warning("ai_policy.refused_remote_model", organization_id=organization_id)
    return False, reason


async def assert_model_allowed(
    db: AsyncSession, organization_id: str, *, settings: Settings | None = None
) -> None:
    allowed, reason = await model_allowed(db, organization_id, settings=settings)
    if not allowed:
        raise ModelNotLocalError(reason or "The configured model is not local.")


def _safe_host(base_url: str | None) -> str:
    """The host, never the path or any key that ended up in the URL."""
    if not base_url:
        return "an unset endpoint"
    return urlparse(base_url).hostname or "an unreadable endpoint"
