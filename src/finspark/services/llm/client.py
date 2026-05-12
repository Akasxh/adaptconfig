"""Async Gemini LLM client using the REST API via httpx.

No SDK dependency — uses httpx against the generativelanguage.googleapis.com v1beta endpoint.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from finspark.core.config import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiAPIError(Exception):
    """Raised when the LLM API (Gemini or OpenAI) returns an error or unexpected response."""


# Provider-neutral alias: code added after the multi-provider switch imports
# this name; keep both working so we don't have to rename every call site.
LLMAPIError = GeminiAPIError


class GeminiClient:
    """Thin async wrapper around the Gemini REST API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key or settings.gemini_api_key
        self.model = model or settings.gemini_model
        self.timeout = timeout

        if not self.api_key:
            raise ValueError(
                "Gemini API key is not set. "
                "Set FINSPARK_GEMINI_API_KEY in .env or pass api_key explicitly."
            )

        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            },
        )

    def _safe_url(self, url: str) -> str:
        """Return URL with the API key redacted."""
        if self.api_key:
            return url.replace(self.api_key, "***")
        return url

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
        """Send a prompt to Gemini and return the text response."""
        url = f"{_BASE_URL}/models/{self.model}:generateContent"

        # Thinking models (Gemini 3+) need extra tokens for internal reasoning
        effective_tokens = max(max_tokens, 256)

        body: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": effective_tokens,
            },
        }

        if system_instruction:
            body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        if response_json:
            body["generationConfig"]["responseMimeType"] = "application/json"

        try:
            resp = await self._client.post(url, json=body)
        except httpx.TimeoutException as exc:
            logger.error("gemini_timeout url=%s", self._safe_url(url))
            raise GeminiAPIError("Gemini API request timed out") from exc
        except httpx.NetworkError as exc:
            logger.error("gemini_network_error url=%s error=%s", self._safe_url(url), exc)
            raise GeminiAPIError(f"Network error communicating with Gemini: {exc}") from exc

        if resp.status_code != 200:
            safe_body = resp.text[:200].replace(self.api_key, "***") if self.api_key else resp.text[:200]
            logger.error("gemini_api_error status=%s body=%s", resp.status_code, safe_body)
            raise GeminiAPIError(
                f"Gemini API returned {resp.status_code}: {safe_body}"
            )

        data = resp.json()
        try:
            candidate = data["candidates"][0]
            # Check for finish reason indicating content was truncated
            finish = candidate.get("finishReason", "")
            content = candidate.get("content", {})
            parts = content.get("parts", [])
            if not parts:
                if finish == "MAX_TOKENS":
                    raise GeminiAPIError(
                        "Gemini response truncated (MAX_TOKENS) — "
                        "thinking model may need higher maxOutputTokens"
                    )
                raise GeminiAPIError(f"Empty response from Gemini (finishReason={finish})")
            return parts[0]["text"]
        except (KeyError, IndexError) as exc:
            logger.error("gemini_unexpected_response data=%s", data)
            raise GeminiAPIError("Unexpected response structure from Gemini") from exc

    async def generate_json(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Generate and parse a JSON response from Gemini."""
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
            logger.error("gemini_json_parse_error text=%s", text[:500])
            raise GeminiAPIError(f"Failed to parse Gemini JSON response: {exc}") from exc


# ---------------------------------------------------------------------------
# Module-level shared client (created lazily, closed by app lifespan)
# ---------------------------------------------------------------------------
# Note: typed as ``Any`` because the actual instance may be either
# ``GeminiClient`` or ``OpenAIClient`` depending on ``settings.llm_provider``.
# Both expose the same ``generate`` / ``generate_json`` / ``close`` surface.
_shared_client: Any | None = None


def get_llm_client() -> Any:
    """Return (or lazily create) the module-level shared LLM client.

    Dispatches on ``settings.llm_provider``:

    * ``"openai"`` → :class:`finspark.services.llm.openai_client.OpenAIClient`
    * anything else (default) → :class:`GeminiClient`

    The lifespan handler in ``main.py`` calls ``_shared_client.close()`` on
    shutdown so the httpx connection pool is released cleanly regardless of
    which provider is active.
    """
    global _shared_client  # noqa: PLW0603
    if _shared_client is None:
        from finspark.core.config import settings as _s  # local import: avoid cycle

        if (_s.llm_provider or "").lower() == "openai":
            from finspark.services.llm.openai_client import OpenAIClient  # noqa: PLC0415

            _shared_client = OpenAIClient()
        else:
            _shared_client = GeminiClient()
    return _shared_client
