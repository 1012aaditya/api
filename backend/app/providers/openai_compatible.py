"""Adapter for any endpoint speaking the OpenAI chat-completions format.

That covers the OpenAI API itself, vLLM, Ollama, LiteLLM, OpenRouter,
Together and most self-hosted gateways — one wire format, selected by
``AI_BASE_URL`` and ``AI_MODEL``.

Failure handling is deliberately conservative (§34): retries are bounded,
only genuinely transient statuses are retried, and an exhausted budget
raises rather than looping.
"""

from __future__ import annotations

import asyncio
import random
import time
from decimal import Decimal
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import ExtractionFailedError, ProviderUnavailableError
from app.core.logging import get_logger
from app.pipelines.stages.preprocess import PreparedDocument, PreparedPage
from app.providers.base import (
    DocumentAIProvider,
    ProviderCapabilities,
    ProviderResult,
    ProviderUsage,
    StructuredResult,
)
from app.providers.json_utils import extract_json_object

logger = get_logger("docuparse.provider.openai_compatible")

_RETRYABLE_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_MILLION = Decimal("1000000")


class OpenAICompatibleProvider(DocumentAIProvider):
    name = "openai_compatible"

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        if not settings.ai_api_key or not settings.ai_model:
            raise ProviderUnavailableError(
                "AI_API_KEY and AI_MODEL must both be set to run extractions."
            )
        self._settings = settings
        self._model = settings.ai_model
        self._base_url = settings.ai_base_url.rstrip("/")
        self._max_retries = max(0, settings.ai_max_retries)
        self._supports_json_mode = True
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.ai_timeout_seconds),
            headers={
                "Authorization": f"Bearer {settings.ai_api_key}",
                "Content-Type": "application/json",
            },
        )

    @property
    def model(self) -> str:
        return self._model

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts_images=True,
            accepts_multiple_images=True,
            supports_json_mode=self._supports_json_mode,
            max_images_per_request=20,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- public API ----------------------------------------------------

    async def extract_text(self, document: PreparedDocument) -> ProviderResult:
        content = _image_content(document.pages)
        content.insert(
            0,
            {
                "type": "text",
                "text": (
                    "Transcribe every line of text visible in this document, "
                    "preserving reading order. Do not summarise, interpret, or "
                    "add anything that is not printed on the page."
                ),
            },
        )
        return await self._complete(
            messages=[{"role": "user", "content": content}], json_mode=False
        )

    async def analyze_image(self, page: PreparedPage, *, prompt: str) -> ProviderResult:
        content = _image_content([page])
        content.insert(0, {"type": "text", "text": prompt})
        return await self._complete(
            messages=[{"role": "user", "content": content}], json_mode=False
        )

    async def extract_structured_data(
        self,
        document: PreparedDocument,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any] | None = None,
    ) -> StructuredResult:
        content = _image_content(document.pages)
        content.insert(0, {"type": "text", "text": user_prompt})

        result = await self._complete(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            json_mode=True,
        )

        parsed = extract_json_object(result.text)
        if parsed is None:
            # The call succeeded but produced nothing usable. Surfacing this
            # as a failure is the point — there is no honest fallback.
            logger.warning(
                "provider.unparseable_response",
                provider=self.name,
                model=self._model,
                finish_reason=result.finish_reason,
                response_chars=len(result.text),
            )
            raise ExtractionFailedError(
                "The extraction model did not return parseable JSON for this document."
            )

        return StructuredResult(
            data=parsed,
            usage=result.usage,
            model=result.model,
            latency_ms=result.latency_ms,
            raw_text=result.text,
        )

    # --- transport -----------------------------------------------------

    async def _complete(
        self, *, messages: list[dict[str, Any]], json_mode: bool
    ) -> ProviderResult:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0,
        }
        if json_mode and self._supports_json_mode:
            payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        response = await self._request_with_retries(payload)
        latency_ms = int((time.perf_counter() - started) * 1000)

        try:
            body = response.json()
            choice = body["choices"][0]
            text = choice["message"]["content"] or ""
            finish_reason = choice.get("finish_reason")
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            logger.warning("provider.malformed_envelope", provider=self.name)
            raise ExtractionFailedError(
                "The extraction provider returned a response in an unexpected format."
            ) from exc

        usage = self._usage_from(body.get("usage") or {})
        logger.info(
            "provider.call_completed",
            provider=self.name,
            model=self._model,
            latency_ms=latency_ms,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            finish_reason=finish_reason,
        )
        return ProviderResult(
            text=text,
            usage=usage,
            model=body.get("model") or self._model,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
        )

    async def _request_with_retries(self, payload: dict[str, Any]) -> httpx.Response:
        url = f"{self._base_url}/chat/completions"
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.post(url, json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt >= self._max_retries:
                    break
                await self._backoff(attempt, reason=type(exc).__name__)
                continue

            if response.status_code < 400:
                return response

            if response.status_code in (400, 422) and "response_format" in response.text:
                # This endpoint does not implement JSON mode. Drop it once and
                # rely on the prompt plus JSON recovery instead.
                if payload.pop("response_format", None) is not None:
                    self._supports_json_mode = False
                    logger.info("provider.json_mode_unsupported", provider=self.name)
                    continue

            if response.status_code in (401, 403):
                logger.error(
                    "provider.auth_rejected", provider=self.name, status=response.status_code
                )
                raise ProviderUnavailableError(
                    "The extraction provider rejected our credentials."
                )

            if response.status_code in _RETRYABLE_STATUSES and attempt < self._max_retries:
                await self._backoff(
                    attempt,
                    reason=f"http_{response.status_code}",
                    retry_after=response.headers.get("retry-after"),
                )
                continue

            logger.error(
                "provider.request_failed", provider=self.name, status=response.status_code
            )
            raise ProviderUnavailableError(
                "The extraction provider returned an error and could not complete the request."
            )

        logger.error(
            "provider.unreachable",
            provider=self.name,
            error=type(last_error).__name__ if last_error else None,
        )
        raise ProviderUnavailableError(
            "The extraction provider could not be reached."
        )

    async def _backoff(
        self, attempt: int, *, reason: str, retry_after: str | None = None
    ) -> None:
        delay = min(2.0**attempt, 8.0) + random.uniform(0, 0.25)
        if retry_after:
            try:
                delay = min(float(retry_after), 30.0)
            except ValueError:
                pass
        logger.info(
            "provider.retrying", provider=self.name, attempt=attempt + 1, reason=reason,
            delay_seconds=round(delay, 2),
        )
        await asyncio.sleep(delay)

    def _usage_from(self, usage: dict[str, Any]) -> ProviderUsage:
        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")
        cost: Decimal | None = None
        if input_tokens is not None or output_tokens is not None:
            cost = (
                Decimal(input_tokens or 0) * self._settings.ai_input_cost_per_mtok
                + Decimal(output_tokens or 0) * self._settings.ai_output_cost_per_mtok
            ) / _MILLION
        return ProviderUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            # Zero configured rates mean "cost unknown", not "free".
            estimated_cost_usd=cost if cost and cost > 0 else None,
        )


def _image_content(pages: list[PreparedPage]) -> list[dict[str, Any]]:
    return [
        {"type": "image_url", "image_url": {"url": page.as_data_url(), "detail": "high"}}
        for page in pages
    ]
