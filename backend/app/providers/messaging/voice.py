"""Voice calls, behind an interface.

A phone call is the most intrusive thing this product does, so the interface
is deliberately narrow: place a call with a script, ask how it went, read what
was said. Nothing here decides *whether* to call — that is the follow-up
ladder's job, and it checks the firm's policy first.

The mock places no calls. It records the attempt and returns a scripted
outcome, so the escalation path is demonstrable and testable without a
telephony account and without anyone's phone ringing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.core.errors import DocuParseError
from app.core.logging import get_logger
from app.utils.ids import prefixed_id

logger = get_logger("docuparse.voice")


@dataclass(frozen=True)
class CallResult:
    ok: bool
    provider_call_id: str | None = None
    status: str = "scheduled"
    error: str | None = None


@dataclass(frozen=True)
class CallOutcome:
    """How a finished call went."""

    status: str
    duration_seconds: int | None = None
    transcript: str | None = None


class VoiceProvider(Protocol):
    name: str

    async def place_call(self, *, to: str, script: str, context: dict) -> CallResult: ...

    async def get_outcome(self, provider_call_id: str) -> CallOutcome | None: ...

    async def end_call(self, provider_call_id: str) -> None: ...

    async def aclose(self) -> None: ...


@dataclass
class PlacedCall:
    to: str
    script: str
    provider_call_id: str
    context: dict = field(default_factory=dict)


class MockVoiceProvider:
    """Records call attempts; returns whatever outcome the test scripted."""

    name = "mock"

    def __init__(self) -> None:
        self.calls: list[PlacedCall] = []
        #: provider_call_id -> the outcome to report when asked.
        self.outcomes: dict[str, CallOutcome] = {}
        #: Set to make every call fail, as a busy line or bad number would.
        self.fail_with: str | None = None

    async def place_call(self, *, to: str, script: str, context: dict) -> CallResult:
        if self.fail_with:
            return CallResult(ok=False, error=self.fail_with, status="failed")
        if not to:
            return CallResult(ok=False, error="No number to call.", status="failed")

        call_id = prefixed_id("mockcall")
        self.calls.append(
            PlacedCall(to=to, script=script, provider_call_id=call_id, context=dict(context))
        )
        logger.info("voice.mock_placed", to_last4=to[-4:])
        return CallResult(ok=True, provider_call_id=call_id, status="dialing")

    async def get_outcome(self, provider_call_id: str) -> CallOutcome | None:
        return self.outcomes.get(provider_call_id)

    async def end_call(self, provider_call_id: str) -> None:
        return None

    async def aclose(self) -> None:
        return None

    # -- test helpers ---------------------------------------------------

    def script_outcome(
        self,
        provider_call_id: str,
        *,
        status: str = "completed",
        transcript: str | None = None,
        duration_seconds: int = 42,
    ) -> None:
        self.outcomes[provider_call_id] = CallOutcome(
            status=status, duration_seconds=duration_seconds, transcript=transcript
        )


class VoiceUnavailableError(DocuParseError):
    code = "voice_provider_unavailable"
    status_code = 503


_FACTORIES = {"mock": lambda _settings: MockVoiceProvider()}
_instance: VoiceProvider | None = None


def register_provider(name: str, factory) -> None:
    _FACTORIES[name] = factory


def build_provider(settings=None) -> VoiceProvider:
    from app.core.config import get_settings

    settings = settings or get_settings()
    if settings.voice_provider == "mock" and settings.is_production:
        # Same reason as the messaging mock: a recorded call that never rang
        # is worse than no call at all.
        raise VoiceUnavailableError(
            "VOICE_PROVIDER=mock places no calls and reports them as placed, "
            "which is not something a production deployment may do."
        )
    factory = _FACTORIES.get(settings.voice_provider)
    if factory is None:
        raise VoiceUnavailableError(
            f"VOICE_PROVIDER={settings.voice_provider!r} is not a provider this build knows."
        )
    return factory(settings)


def get_provider() -> VoiceProvider:
    global _instance
    if _instance is None:
        _instance = build_provider()
    return _instance


def set_provider(provider: VoiceProvider | None) -> None:
    global _instance
    _instance = provider
