"""LLM service — Gemini-powered text generation for config and document analysis."""

from finspark.services.llm.client import GeminiClient, get_llm_client

__all__ = ["GeminiClient", "get_llm_client"]
