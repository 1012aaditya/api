"""A WhatsApp provider that sends nothing and records everything.

This is what demo mode runs on (§31), and what every test runs on. It exists
so the whole product — chasing, following up, receiving documents, escalating
— can be demonstrated end to end without a Meta account, a BSP contract, or a
single real message reaching a real client.

Two rules keep it honest:

* **It never claims a send that a real provider would refuse.** An empty body,
  a missing number, or a number that is obviously not a phone number comes
  back as ``ok=False``, exactly as it would in production.
* **It is never selected by accident.** ``WHATSAPP_PROVIDER`` must name it.
  There is no silent fallback to a mock when credentials are missing — a
  deployment that thinks it is messaging clients and is not would be far worse
  than one that refuses to start.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.providers.messaging.base import InboundMessage, SendResult
from app.utils.ids import prefixed_id

logger = get_logger("docuparse.whatsapp.mock")

#: Deliberately loose: Indian mobile numbers arrive in several shapes, and this
#: only has to reject what is plainly not a number at all.
_PHONE = re.compile(r"^\+?\d[\d\s\-()]{7,19}$")


@dataclass
class SentMessage:
    """One message the mock would have sent. Inspected by tests and the demo."""

    to: str
    body: str
    provider_message_id: str
    kind: str = "text"
    filename: str | None = None
    template: str | None = None
    variables: dict[str, str] = field(default_factory=dict)


class MockWhatsAppProvider:
    """Records outbound messages; serves media the test put in it."""

    name = "mock"

    def __init__(self) -> None:
        self.sent: list[SentMessage] = []
        #: media_reference -> bytes, as though the client had uploaded them.
        self.media: dict[str, bytes] = {}

    # -- outbound -------------------------------------------------------

    def _refuse(self, reason: str) -> SendResult:
        return SendResult(ok=False, error=reason)

    def _check(self, to: str, body: str | None) -> SendResult | None:
        if not to or not _PHONE.match(to.strip()):
            return self._refuse(f"{to!r} is not a usable phone number.")
        if body is not None and not body.strip():
            return self._refuse("Refusing to send an empty message.")
        return None

    async def send_message(self, *, to: str, body: str) -> SendResult:
        refusal = self._check(to, body)
        if refusal is not None:
            return refusal
        message = SentMessage(
            to=to, body=body, provider_message_id=prefixed_id("wamid")
        )
        self.sent.append(message)
        logger.info("whatsapp.mock_sent", to_last4=to[-4:], length=len(body))
        return SendResult(ok=True, provider_message_id=message.provider_message_id)

    async def send_template(
        self, *, to: str, template: str, variables: dict[str, str]
    ) -> SendResult:
        refusal = self._check(to, None)
        if refusal is not None:
            return refusal
        rendered = template
        for key, value in variables.items():
            rendered = rendered.replace(f"{{{{{key}}}}}", value)
        message = SentMessage(
            to=to,
            body=rendered,
            provider_message_id=prefixed_id("wamid"),
            kind="template",
            template=template,
            variables=dict(variables),
        )
        self.sent.append(message)
        return SendResult(ok=True, provider_message_id=message.provider_message_id)

    async def send_document(
        self, *, to: str, content: bytes, filename: str, caption: str | None = None
    ) -> SendResult:
        refusal = self._check(to, None)
        if refusal is not None:
            return refusal
        if not content:
            return self._refuse("Refusing to send an empty document.")
        message = SentMessage(
            to=to,
            body=caption or "",
            provider_message_id=prefixed_id("wamid"),
            kind="document",
            filename=filename,
        )
        self.sent.append(message)
        return SendResult(ok=True, provider_message_id=message.provider_message_id)

    # -- inbound --------------------------------------------------------

    async def fetch_media(self, media_reference: str) -> bytes:
        try:
            return self.media[media_reference]
        except KeyError as exc:
            raise FileNotFoundError(
                f"No media is registered under {media_reference!r}."
            ) from exc

    def parse_webhook(self, payload: dict) -> list[InboundMessage]:
        """Read the shape this mock's own webhooks use.

        Kept close to Meta's envelope — entry → changes → value → messages —
        so that swapping in the real adapter changes the parsing and nothing
        that depends on it.
        """
        messages: list[InboundMessage] = []
        for entry in payload.get("entry", []) or []:
            for change in entry.get("changes", []) or []:
                value = change.get("value") or {}
                for raw in value.get("messages", []) or []:
                    kind = raw.get("type", "text")
                    body = None
                    media_reference = None
                    filename = None
                    mime_type = None

                    if kind == "text":
                        body = (raw.get("text") or {}).get("body")
                    elif kind in ("document", "image"):
                        media = raw.get(kind) or {}
                        media_reference = media.get("id")
                        filename = media.get("filename")
                        mime_type = media.get("mime_type")
                        body = media.get("caption")

                    identifier = raw.get("id")
                    sender = raw.get("from")
                    if not identifier or not sender:
                        # A message with no id cannot be deduplicated and a
                        # message with no sender cannot be attributed. Skipping
                        # beats guessing.
                        continue

                    messages.append(
                        InboundMessage(
                            provider_message_id=identifier,
                            from_phone=sender,
                            type=kind,
                            body=body,
                            media_reference=media_reference,
                            filename=filename,
                            mime_type=mime_type,
                            timestamp=raw.get("timestamp"),
                        )
                    )
        return messages

    async def aclose(self) -> None:
        return None

    # -- test and demo helpers -----------------------------------------

    def register_media(self, reference: str, content: bytes) -> None:
        self.media[reference] = content

    @staticmethod
    def webhook_for_text(*, message_id: str, from_phone: str, body: str) -> dict:
        return {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": message_id,
                                        "from": from_phone,
                                        "type": "text",
                                        "text": {"body": body},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }

    @staticmethod
    def webhook_for_document(
        *,
        message_id: str,
        from_phone: str,
        media_reference: str,
        filename: str,
        mime_type: str = "application/pdf",
        caption: str | None = None,
    ) -> dict:
        return {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": message_id,
                                        "from": from_phone,
                                        "type": "document",
                                        "document": {
                                            "id": media_reference,
                                            "filename": filename,
                                            "mime_type": mime_type,
                                            "caption": caption,
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
