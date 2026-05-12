"""Async OpenAI Chat Completions client.

Surface-compatible with :class:`finspark.services.llm.client.GeminiClient` —
exposes the same ``generate`` / ``generate_json`` methods so call sites that
were written against the Gemini client keep working unchanged.

``OpenAIAPIError`` subclasses ``GeminiAPIError`` so ``except GeminiAPIError``
clauses scattered across the codebase keep catching provider errors regardless
of which client is active.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from finspark.core.config import settings
from finspark.services.llm.client import GeminiAPIError

logger = logging.getLogger(__name__)

_CHAT_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIAPIError(GeminiAPIError):
    """Raised when the OpenAI API returns an error or unexpected response.

    Subclass of GeminiAPIError so the many ``except GeminiAPIError`` blocks
    written when this codebase was Gemini-only continue to catch failures.
    """


class OpenAIClient:
    """Thin async wrapper around the OpenAI Chat Completions REST API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key or settings.openai_api_key
        self.model = model or (settings.llm_model or "gpt-4.1-mini")
        self.timeout = timeout

        if not self.api_key:
            raise ValueError(
                "OpenAI API key is not set. "
                "Set FINSPARK_OPENAI_API_KEY in .env or pass api_key explicitly."
            )

        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def generate(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        response_json: bool = False,
    ) -> str:
        """Send a prompt to OpenAI and return the assistant text content."""
        messages: list[dict[str, str]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_json:
            body["response_format"] = {"type": "json_object"}

        try:
            resp = await self._client.post(_CHAT_URL, json=body)
        except httpx.TimeoutException as exc:
            logger.error("openai_timeout model=%s", self.model)
            raise OpenAIAPIError("OpenAI API request timed out") from exc
        except httpx.NetworkError as exc:
            logger.error("openai_network_error model=%s error=%s", self.model, exc)
            raise OpenAIAPIError(f"Network error communicating with OpenAI: {exc}") from exc

        if resp.status_code != 200:
            safe_body = resp.text[:300]
            logger.error("openai_api_error status=%s body=%s", resp.status_code, safe_body)
            raise OpenAIAPIError(f"OpenAI API returned {resp.status_code}: {safe_body}")

        data = resp.json()
        try:
            choice = data["choices"][0]
            finish = choice.get("finish_reason", "")
            content = (choice.get("message") or {}).get("content")
            if not content:
                if finish == "length":
                    raise OpenAIAPIError(
                        "OpenAI response truncated (finish_reason=length) — increase max_tokens"
                    )
                raise OpenAIAPIError(f"Empty response from OpenAI (finish_reason={finish})")
            return content
        except (KeyError, IndexError) as exc:
            logger.error("openai_unexpected_response data=%s", data)
            raise OpenAIAPIError("Unexpected response structure from OpenAI") from exc

    async def generate_json(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Generate and parse a JSON response from OpenAI."""
        text = await self.generate(
            prompt,
            system_instruction=system_instruction,
            temperature=temperature,
            max_tokens=max_tokens,
            response_json=True,
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.error("openai_json_parse_error text=%s", text[:500])
            raise OpenAIAPIError(f"Failed to parse OpenAI JSON response: {exc}") from exc
