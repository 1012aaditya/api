"""What a messaging provider must do, and nothing about who provides it.

The business logic never imports a vendor. It sends through this interface,
and the deployment decides whether that reaches Meta's Cloud API, a BSP, or —
in demo mode and in every test — a mock that records what would have been sent
(§7, §32).

Two things are deliberately part of the interface rather than left to each
adapter:

* **The provider returns an id.** Everything downstream — delivery receipts,
  webhook idempotency, "did the client actually get this" — keys off it.
* **A failure is an outcome, not an exception.** A message that could not be
  sent is a fact the CA needs on the timeline, so it comes back as a result
  with ``ok=False`` rather than an exception that unwinds the run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class SendResult:
    """What happened when we tried to send."""

    ok: bool
    provider_message_id: str | None = None
    error: str | None = None
    #: Anything vendor-specific worth keeping, already redacted.
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class InboundMessage:
    """A message from a client, normalised out of a provider's webhook shape."""

    provider_message_id: str
    from_phone: str
    #: "text" | "document" | "image"
    type: str = "text"
    body: str | None = None
    #: A provider-side handle for the media. Never a public URL.
    media_reference: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    timestamp: str | None = None


class WhatsAppProvider(Protocol):
    """Send to, and receive from, a client on WhatsApp."""

    name: str

    async def send_message(self, *, to: str, body: str) -> SendResult: ...

    async def send_template(
        self, *, to: str, template: str, variables: dict[str, str]
    ) -> SendResult: ...

    async def send_document(
        self, *, to: str, content: bytes, filename: str, caption: str | None = None
    ) -> SendResult: ...

    async def fetch_media(self, media_reference: str) -> bytes:
        """Download what a client sent. Raises if it cannot."""
        ...

    def parse_webhook(self, payload: dict) -> list[InboundMessage]:
        """Turn one webhook body into zero or more messages.

        Returns a list because providers batch. Returns an empty list for
        payloads that carry no message — delivery receipts, status updates —
        rather than raising, because those are normal traffic.
        """
        ...

    async def aclose(self) -> None: ...
