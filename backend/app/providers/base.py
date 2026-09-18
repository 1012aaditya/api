"""The AI provider seam (§12).

Nothing above this module knows which vendor is in use, what its wire format
looks like, or how it charges. Swapping providers means adding a file here
and changing ``AI_PROVIDER`` — not touching the pipeline, the schema, or the
validators.

Two rules every adapter must honour:

1. It reports its own token usage and estimated cost, so §34 cost control
   works regardless of vendor.
2. It raises ``ProviderUnavailableError`` or ``ExtractionFailedError`` — it
   never returns a fabricated or partially-invented payload to stand in for
   a failed call (§42).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.pipelines.stages.preprocess import PreparedDocument, PreparedPage


@dataclass(frozen=True)
class ProviderUsage:
    """What one provider call consumed. Every field may be None if unreported."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: Decimal | None = None

    def merged_with(self, other: ProviderUsage) -> ProviderUsage:
        def add(a: int | None, b: int | None) -> int | None:
            if a is None and b is None:
                return None
            return (a or 0) + (b or 0)

        def add_cost(a: Decimal | None, b: Decimal | None) -> Decimal | None:
            if a is None and b is None:
                return None
            return (a or Decimal("0")) + (b or Decimal("0"))

        return ProviderUsage(
            input_tokens=add(self.input_tokens, other.input_tokens),
            output_tokens=add(self.output_tokens, other.output_tokens),
            estimated_cost_usd=add_cost(self.estimated_cost_usd, other.estimated_cost_usd),
        )


@dataclass(frozen=True)
class ProviderResult:
    """A raw provider response, before any DocuParse schema is applied."""

    text: str
    usage: ProviderUsage
    model: str
    latency_ms: int
    finish_reason: str | None = None


@dataclass(frozen=True)
class StructuredResult:
    """A provider response already parsed as JSON."""

    data: dict[str, Any]
    usage: ProviderUsage
    model: str
    latency_ms: int
    raw_text: str = field(repr=False, default="")


@dataclass(frozen=True)
class ProviderCapabilities:
    accepts_images: bool = True
    accepts_multiple_images: bool = True
    supports_json_mode: bool = False
    max_images_per_request: int = 20


class DocumentAIProvider(abc.ABC):
    """The interface every adapter implements."""

    name: str = "base"

    @property
    @abc.abstractmethod
    def model(self) -> str:
        """The concrete model identifier this instance calls."""

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    @abc.abstractmethod
    async def extract_text(self, document: PreparedDocument) -> ProviderResult:
        """Read the document and return its text as the model perceives it."""

    @abc.abstractmethod
    async def extract_structured_data(
        self,
        document: PreparedDocument,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any] | None = None,
    ) -> StructuredResult:
        """Read the document and return JSON matching the requested shape."""

    @abc.abstractmethod
    async def analyze_image(self, page: PreparedPage, *, prompt: str) -> ProviderResult:
        """Answer a free-form question about one page."""

    async def aclose(self) -> None:  # noqa: B027 — optional hook, not every adapter holds a connection
        """Release connections. Safe to call more than once."""
