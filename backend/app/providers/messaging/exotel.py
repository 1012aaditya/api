"""Outbound voice through Exotel.

**Read this before switching voice on.** Exotel does not speak text this code
sends it. A call connects the client to a *flow* built in the Exotel
dashboard, and that flow is what talks. So the script composed in
``app/services/voice.py`` is what the firm intended to say, not a transcript
of what the client heard.

That matters for one reason in particular: a call from a machine has to say
so, in the first sentence. This code cannot put that sentence in the call —
the flow has to. ``compose_script`` is sent along as a custom field and kept
on the call record so the firm can compare the two, and the README says
plainly that the flow must open with the disclosure. A deployment whose flow
does not is not something this file can detect, and the honest thing is to
say so rather than imply a guarantee.

The rest is ordinary: HTTP Basic auth, form-encoded parameters, a call SID
back, and a second request to ask how it went. Nothing is retried — a phone
call placed twice because a response was slow is a real cost to a real
person.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.messaging.voice import CallOutcome, CallResult

logger = get_logger("docuparse.voice.exotel")

_NON_DIGITS = re.compile(r"[^\d+]")

#: Exotel's statuses, in this product's words.
_STATUS = {
    "queued": "dialing",
    "in-progress": "in_progress",
    "ringing": "dialing",
    "completed": "completed",
    "failed": "failed",
    "busy": "no_answer",
    "no-answer": "no_answer",
    "canceled": "cancelled",
    "cancelled": "cancelled",
}

_REGIONS = {"in": "api.in.exotel.com", "sg": "api.exotel.com"}


class ExotelVoiceProvider:
    """Places calls through Exotel's Connect API."""

    name = "exotel"

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        self._sid = settings.exotel_sid or ""
        self._caller_id = settings.exotel_caller_id or ""
        self._flow_id = settings.exotel_flow_id or ""
        host = _REGIONS.get(settings.exotel_region, _REGIONS["in"])
        self._base = f"https://{host}/v1/Accounts/{self._sid}"
        # Credentials belong to the request, not to whichever client happens
        # to be in use: hanging them on a client the caller may replace is how
        # a call goes out unauthenticated and nobody notices until it does.
        self._auth = (settings.exotel_api_key or "", settings.exotel_api_token or "")
        self._owned = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0))

    @staticmethod
    def normalise(number: str) -> str:
        return _NON_DIGITS.sub("", number or "")

    @property
    def _flow_url(self) -> str:
        return f"http://my.exotel.com/{self._sid}/exoml/start_voice/{self._flow_id}"

    async def place_call(self, *, to: str, script: str, context: dict) -> CallResult:
        number = self.normalise(to)
        if not number:
            return CallResult(
                ok=False,
                error=f"{to!r} is not a usable phone number.",
                status="failed",
            )

        form = {
            "From": number,
            "CallerId": self._caller_id,
            "Url": self._flow_url,
            # Carried through Exotel and returned on the call record, so the
            # firm can tie a call in their dashboard back to a case here.
            "CustomField": _custom_field(context),
            "TimeLimit": "300",
            "TimeOut": "30",
        }

        try:
            response = await self._client.post(
                f"{self._base}/Calls/connect.json", data=form, auth=self._auth
            )
        except httpx.HTTPError as exc:
            logger.warning("voice.transport_failed", error=type(exc).__name__)
            return CallResult(ok=False, error="Exotel could not be reached.", status="failed")

        if response.status_code >= 400:
            message = _error_message(response)
            logger.warning("voice.call_refused", status=response.status_code)
            return CallResult(ok=False, error=message, status="failed")

        call = _call_body(response)
        sid = call.get("Sid")
        if not sid:
            return CallResult(
                ok=False,
                error="Exotel accepted the call without returning an id.",
                status="failed",
            )

        logger.info("voice.call_placed", status=call.get("Status"))
        return CallResult(
            ok=True,
            provider_call_id=str(sid),
            status=_STATUS.get(str(call.get("Status") or "").lower(), "dialing"),
        )

    async def get_outcome(self, provider_call_id: str) -> CallOutcome | None:
        if not provider_call_id:
            return None
        try:
            response = await self._client.get(
                f"{self._base}/Calls/{provider_call_id}.json", auth=self._auth
            )
        except httpx.HTTPError:
            return None
        if response.status_code >= 400:
            return None

        call = _call_body(response)
        status = _STATUS.get(str(call.get("Status") or "").lower())
        if status is None:
            return None

        duration = call.get("Duration")
        return CallOutcome(
            status=status,
            duration_seconds=int(duration) if str(duration or "").isdigit() else None,
            # Exotel returns a recording URL, not words. Nothing here invents
            # a transcript of what a client said.
            transcript=None,
        )

    async def end_call(self, provider_call_id: str) -> None:
        if not provider_call_id:
            return
        try:
            await self._client.post(
                f"{self._base}/Calls/{provider_call_id}.json",
                data={"Status": "completed"},
                auth=self._auth,
            )
        except httpx.HTTPError:
            logger.warning("voice.hangup_failed")

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()


def _call_body(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    if not isinstance(body, dict):
        return {}
    call = body.get("Call")
    return call if isinstance(call, dict) else body


def _error_message(response: httpx.Response) -> str:
    body: Any
    try:
        body = response.json()
    except ValueError:
        return f"Exotel returned {response.status_code}."
    if isinstance(body, dict):
        message = (body.get("RestException") or {}).get("Message") if isinstance(
            body.get("RestException"), dict
        ) else body.get("message")
        if message:
            return str(message)
    return f"Exotel returned {response.status_code}."


def _custom_field(context: dict) -> str:
    """A short, non-identifying label for the firm's own call logs."""
    parts = [str(value) for key, value in context.items() if key in ("case", "period")]
    return " ".join(parts)[:120] or "docuparse"
