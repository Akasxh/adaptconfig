"""Document parsing LLM provider gate tests."""

from finspark.api.routes import documents


def test_openai_document_parsing_does_not_require_gemini_key(monkeypatch) -> None:
    """OpenAI mode should use the OpenAI key instead of the legacy Gemini key."""
    monkeypatch.setattr(documents.settings, "ai_enabled", True)
    monkeypatch.setattr(documents.settings, "llm_provider", "openai")
    monkeypatch.setattr(documents.settings, "openai_api_key", "test-openai-key")
    monkeypatch.setattr(documents.settings, "gemini_api_key", "")

    assert documents._llm_parsing_enabled() is True


def test_document_parsing_llm_gate_respects_ai_enabled(monkeypatch) -> None:
    monkeypatch.setattr(documents.settings, "ai_enabled", False)
    monkeypatch.setattr(documents.settings, "llm_provider", "openai")
    monkeypatch.setattr(documents.settings, "openai_api_key", "test-openai-key")

    assert documents._llm_parsing_enabled() is False
