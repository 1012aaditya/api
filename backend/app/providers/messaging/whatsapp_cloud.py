"""WhatsApp Business Cloud API.

The real adapter. Everything above it — the ladder, the agent, the exception
engine — is unchanged by its existence, because it implements the same five
methods the mock does.

Three things about WhatsApp that shape this file:

* **You may not message anyone you like.** Free-form text is allowed only
  within 24 hours of the client's last message. Outside that window Meta
  refuses the send (error 131047) and the first contact has to be a template
  the business got approved beforehand. That refusal comes back as an
  ordinary failed ``SendResult`` with a reason the CA can read, not an
  exception, and the follow-up engine records it and stops rather than
  retrying into a wall.
* **Media arrives in two hops.** A webhook carries a media id; the bytes need
  one call to resolve a URL and another, authenticated, to download it.
* **The token is a bearer token for the whole WhatsApp Business account.** It
  is never logged, never put in an error message, and never returned to a
  caller (§23).

Not run against Meta from this repository. The request shapes here follow the
Cloud API documentation and are covered by tests against a scripted
transport, which is not the same thing as a message arriving on a phone —
that has to be confirmed once with a real account before anyone relies on it.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.messaging.base import InboundMessage, SendResult
from app.providers.messaging.meta import parse_meta_webhook

logger = get_logger("docuparse.whatsapp.cloud")

#: Meta wants digits only: country code, no plus, no spaces.
_NON_DIGITS = re.compile(r"\D+")

#: "Message failed to send because more than 24 hours have passed since the
#: customer last replied." Worth naming, because the answer is a template
#: rather than a retry.
OUTSIDE_WINDOW = 131047

#: Failures where sending the same thing again will not help.
_PERMANENT = {
    131047,  # outside the 24-hour window
    131026,  # not a WhatsApp user / undeliverable
    131051,  # unsupported message type
    132000,  # template parameter count mismatch
    132001,  # template does not exist
    133010,  # the number is not registered
}


class WhatsAppCloudProvider:
    """Sends through Meta's Cloud API."""

    name = "whatsapp_cloud"

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        self._token = settings.whatsapp_access_token or ""
        self._phone_number_id = settings.whatsapp_phone_number_id or ""
        self._base = settings.whatsapp_api_base.rstrip("/")
        self._version = settings.whatsapp_api_version
        self._max_bytes = settings.max_file_size_bytes
        self._template_language = settings.whatsapp_template_language
        self._owned = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0))

    # -- plumbing --------------------------------------------------------

    @property
    def _messages_url(self) -> str:
        return f"{self._base}/{self._version}/{self._phone_number_id}/messages"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    @staticmethod
    def normalise(number: str) -> str:
        return _NON_DIGITS.sub("", number or "")

    async def _post(self, payload: dict[str, Any]) -> SendResult:
        """One send, with every failure turned into an outcome."""
        try:
            response = await self._client.post(
                self._messages_url, headers=self._headers, json=payload
            )
        except httpx.HTTPError as exc:
            # The network, not the message. Worth retrying later.
            logger.warning("whatsapp.transport_failed", error=type(exc).__name__)
            return SendResult(ok=False, error="WhatsApp could not be reached.")

        if response.status_code >= 400:
            return self._failure(response)

        body = self._json(response)
        identifier = None
        messages = body.get("messages") or []
        if messages:
            identifier = messages[0].get("id")
        if not identifier:
            # A 200 with no id is not a send anyone can track later.
            logger.warning("whatsapp.no_message_id")
            return SendResult(ok=False, error="WhatsApp accepted the request without an id.")

        logger.info("whatsapp.sent", kind=payload.get("type"))
        return SendResult(ok=True, provider_message_id=identifier)

    def _failure(self, response: httpx.Response) -> SendResult:
        error = (self._json(response).get("error") or {}) if response.content else {}
        code = error.get("code")
        message = error.get("message") or f"WhatsApp returned {response.status_code}."
        # Meta's own wording is written for developers; the CA reads this.
        if code == OUTSIDE_WINDOW:
            message = (
                "WhatsApp does not allow a free-form message more than 24 hours "
                "after the client last replied. An approved template is needed "
                "to start the conversation again."
            )
        logger.warning(
            "whatsapp.send_failed", status=response.status_code, code=code
        )
        return SendResult(
            ok=False,
            error=message,
            details={
                "code": code,
                "permanent": code in _PERMANENT,
                # Vendor-neutral, so the messaging service can act on it
                # without importing this module or knowing Meta's numbers.
                "needs_template": code == OUTSIDE_WINDOW,
            },
        )

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    # -- outbound --------------------------------------------------------

    async def send_message(self, *, to: str, body: str) -> SendResult:
        number = self.normalise(to)
        if not number:
            return SendResult(ok=False, error=f"{to!r} is not a usable phone number.")
        if not body.strip():
            return SendResult(ok=False, error="Refusing to send an empty message.")

        return await self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": number,
                "type": "text",
                # Link previews off: a client's document link is not ours to
                # render, and the preview fetch leaves our servers.
                "text": {"preview_url": False, "body": body},
            }
        )

    async def send_template(
        self, *, to: str, template: str, variables: dict[str, str]
    ) -> SendResult:
        number = self.normalise(to)
        if not number:
            return SendResult(ok=False, error=f"{to!r} is not a usable phone number.")

        # Meta's template parameters are positional. The caller's dict is
        # ordered, and sorting by key would silently reorder a template's
        # placeholders, so insertion order is what gets sent.
        parameters = [
            {"type": "text", "text": str(value)} for value in variables.values()
        ]
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": number,
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": self._template_language},
            },
        }
        if parameters:
            payload["template"]["components"] = [
                {"type": "body", "parameters": parameters}
            ]
        return await self._post(payload)

    async def send_document(
        self, *, to: str, content: bytes, filename: str, caption: str | None = None
    ) -> SendResult:
        number = self.normalise(to)
        if not number:
            return SendResult(ok=False, error=f"{to!r} is not a usable phone number.")
        if not content:
            return SendResult(ok=False, error="Refusing to send an empty document.")

        uploaded = await self._upload(content, filename=filename)
        if uploaded is None:
            return SendResult(ok=False, error="The file could not be uploaded to WhatsApp.")

        document: dict[str, Any] = {"id": uploaded, "filename": filename}
        if caption:
            document["caption"] = caption
        return await self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": number,
                "type": "document",
                "document": document,
            }
        )

    async def _upload(self, content: bytes, *, filename: str) -> str | None:
        url = f"{self._base}/{self._version}/{self._phone_number_id}/media"
        try:
            response = await self._client.post(
                url,
                headers=self._headers,
                data={"messaging_product": "whatsapp"},
                files={"file": (filename, content, "application/pdf")},
            )
        except httpx.HTTPError as exc:
            logger.warning("whatsapp.upload_transport_failed", error=type(exc).__name__)
            return None
        if response.status_code >= 400:
            logger.warning("whatsapp.upload_failed", status=response.status_code)
            return None
        return self._json(response).get("id")

    # -- inbound ---------------------------------------------------------

    async def fetch_media(self, media_reference: str) -> bytes:
        """Resolve the media id to a URL, then download it.

        Raises rather than returning empty bytes: the caller files an
        exception for the firm when a client's file cannot be downloaded, and
        an empty PDF would be classified as an unreadable document instead —
        the wrong problem, reported to the wrong person.
        """
        if not media_reference:
            raise FileNotFoundError("No media reference was given.")

        lookup = await self._client.get(
            f"{self._base}/{self._version}/{media_reference}", headers=self._headers
        )
        if lookup.status_code >= 400:
            raise FileNotFoundError(
                f"WhatsApp would not resolve media {media_reference!r} "
                f"({lookup.status_code})."
            )
        url = self._json(lookup).get("url")
        if not url:
            raise FileNotFoundError(f"Media {media_reference!r} has no download URL.")

        # The download host is Meta's, but the URL comes from a response, so
        # the size is enforced here rather than trusted.
        async with self._client.stream("GET", url, headers=self._headers) as download:
            if download.status_code >= 400:
                raise FileNotFoundError(
                    f"Media {media_reference!r} could not be downloaded "
                    f"({download.status_code})."
                )
            chunks: list[bytes] = []
            total = 0
            async for chunk in download.aiter_bytes():
                total += len(chunk)
                if total > self._max_bytes:
                    raise ValueError(
                        f"The file is larger than the {self._max_bytes} byte limit."
                    )
                chunks.append(chunk)
        return b"".join(chunks)

    def parse_webhook(self, payload: dict) -> list[InboundMessage]:
        return parse_meta_webhook(payload)

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()
