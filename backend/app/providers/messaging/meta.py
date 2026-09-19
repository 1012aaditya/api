"""Reading Meta's webhook envelope.

One parser, used by both the Cloud API adapter and the mock. The mock's whole
value is that it behaves like the real thing, and two copies of this would
drift until the day a real deployment found out.

Meta's shape: entry → changes → value → messages, with the interesting fields
in a sub-object named after the message type. Anything else in that envelope —
delivery receipts, read receipts, account updates — carries no message and
comes back as an empty list rather than an error, because it is normal
traffic.
"""

from __future__ import annotations

from app.providers.messaging.base import InboundMessage

#: Message types that carry a file we can fetch.
MEDIA_TYPES = ("document", "image", "audio", "video")


def parse_meta_webhook(payload: dict) -> list[InboundMessage]:
    messages: list[InboundMessage] = []

    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            for raw in value.get("messages", []) or []:
                message = _one(raw)
                if message is not None:
                    messages.append(message)
    return messages


def _one(raw: dict) -> InboundMessage | None:
    identifier = raw.get("id")
    sender = raw.get("from")
    if not identifier or not sender:
        # A message with no id cannot be deduplicated and one with no sender
        # cannot be attributed. Skipping beats guessing.
        return None

    kind = raw.get("type", "text")
    body = None
    media_reference = None
    filename = None
    mime_type = None

    if kind == "text":
        body = (raw.get("text") or {}).get("body")
    elif kind in MEDIA_TYPES:
        media = raw.get(kind) or {}
        media_reference = media.get("id")
        filename = media.get("filename")
        mime_type = media.get("mime_type")
        body = media.get("caption")
    elif kind == "button":
        # Quick-reply buttons on a template. The text is what they pressed.
        body = (raw.get("button") or {}).get("text")
    elif kind == "interactive":
        interactive = raw.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        body = reply.get("title")

    return InboundMessage(
        provider_message_id=identifier,
        from_phone=sender,
        type=kind,
        body=body,
        media_reference=media_reference,
        filename=filename,
        mime_type=mime_type,
        timestamp=raw.get("timestamp"),
    )
