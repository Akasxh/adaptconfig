"""Tests for the Gemini LLM client."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from finspark.services.llm.client import GeminiAPIError, GeminiClient


@pytest.fixture()
def gemini_client() -> GeminiClient:
    return GeminiClient(api_key="test-key", model="gemini-2.5-flash")


def _mock_gemini_response(text: str) -> httpx.Response:
    """Build a fake Gemini API response."""
    return httpx.Response(
        status_code=200,
        json={
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": text}],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "totalTokenCount": 15,
            },
        },
    )


def _mock_error_response(status: int, message: str) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json={"error": {"code": status, "message": message, "status": "ERROR"}},
    )


class TestGeminiClient:
    def test_init_requires_api_key(self) -> None:
        with patch("finspark.services.llm.client.settings") as mock_settings:
            mock_settings.GEMINI_API_KEY = ""
            mock_settings.GEMINI_MODEL = "gemini-2.5-flash"
            with pytest.raises(ValueError, match="GEMINI_API_KEY is not set"):
                GeminiClient()

    def test_init_with_explicit_key(self) -> None:
        client = GeminiClient(api_key="my-key", model="gemini-2.5-pro")
        assert client.api_key == "my-key"
        assert client.model == "gemini-2.5-pro"

    async def test_generate_returns_text(self, gemini_client: GeminiClient) -> None:
        mock_resp = _mock_gemini_response("Hello world")

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
            result = await gemini_client.generate("Say hello")

        assert result == "Hello world"

    async def test_generate_with_system_instruction(
        self, gemini_client: GeminiClient
    ) -> None:
        mock_resp = _mock_gemini_response("Structured output")

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
            await gemini_client.generate(
                "Generate config",
                system_instruction="You are FinSpark",
            )

        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert "systemInstruction" in body

    async def test_generate_api_error(self, gemini_client: GeminiClient) -> None:
        mock_resp = _mock_error_response(403, "API key invalid")

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(GeminiAPIError, match="403"):
                await gemini_client.generate("test")

    async def test_generate_json_returns_dict(
        self, gemini_client: GeminiClient
    ) -> None:
        payload = {"base_url": "https://api.example.com", "timeout_ms": 3000}
        mock_resp = _mock_gemini_response(json.dumps(payload))

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
            result = await gemini_client.generate_json("Generate config")

        assert result == payload

    async def test_generate_json_bad_json(self, gemini_client: GeminiClient) -> None:
        mock_resp = _mock_gemini_response("not valid json {{{")

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(GeminiAPIError, match="Failed to parse"):
                await gemini_client.generate_json("Generate config")

    async def test_generate_unexpected_structure(
        self, gemini_client: GeminiClient
    ) -> None:
        bad_resp = httpx.Response(
            status_code=200,
            json={"candidates": []},
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=bad_resp):
            with pytest.raises(GeminiAPIError, match="Unexpected response"):
                await gemini_client.generate("test")
