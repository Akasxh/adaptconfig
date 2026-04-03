"""Async Gemini LLM client using the REST API via httpx.

No SDK dependency — just httpx (already in deps) against the
generativelanguage.googleapis.com v1beta endpoint.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import structlog

from finspark.core.config import settings

logger = structlog.get_logger(__name__)

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiClient:
    """Thin async wrapper around the Gemini REST API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.model = model or settings.GEMINI_MODEL
        self.timeout = timeout

        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY is not set. "
                "Add it to .env or pass api_key explicitly."
            )

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

        body: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }

        if system_instruction:
            body["systemInstruction"] = {
                "parts": [{"text": system_instruction}],
            }

        if response_json:
            body["generationConfig"]["responseMimeType"] = "application/json"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                params={"key": self.api_key},
                json=body,
            )

        if resp.status_code != 200:
            logger.error(
                "gemini_api_error",
                status=resp.status_code,
                body=resp.text[:500],
            )
            raise GeminiAPIError(
                f"Gemini API returned {resp.status_code}: {resp.text[:300]}"
            )

        data = resp.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            logger.error("gemini_unexpected_response", data=data)
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
            logger.error("gemini_json_parse_error", text=text[:500])
            raise GeminiAPIError(f"Failed to parse Gemini JSON response: {exc}") from exc


class GeminiAPIError(Exception):
    """Raised when the Gemini API returns an error or unexpected response."""


_client: GeminiClient | None = None


def get_llm_client() -> GeminiClient:
    """Singleton accessor for the Gemini client."""
    global _client  # noqa: PLW0603
    if _client is None:
        _client = GeminiClient()
    return _client
