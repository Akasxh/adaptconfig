"""LLM-powered entity extraction for free-text documents (BRD/SOW)."""

import logging
from typing import Any

from finspark.core.config import settings
from finspark.services.llm.client import get_llm_client

logger = logging.getLogger(__name__)

_SYSTEM_INSTRUCTION = (
    "You are an expert at extracting structured integration requirements from "
    "enterprise Business Requirement Documents (BRDs) and Statements of Work (SOWs) "
    "for Indian financial services. Extract API endpoints, field definitions, "
    "authentication requirements, and service identifiers."
)

_EXTRACTION_PROMPT = """Analyze this document text and extract structured information.

Document text:
---
{text}
---

Return a JSON object with these keys:
{{
  "title": "Document title or best guess",
  "summary": "One-line summary of the document's purpose",
  "services_identified": ["List of service/API names mentioned"],
  "endpoints": [
    {{"path": "/api/path", "method": "POST", "description": "What it does", "is_mandatory": true}}
  ],
  "fields": [
    {{"name": "field_name", "data_type": "string", "is_required": true, "source_section": "section name"}}
  ],
  "auth_requirements": [
    {{"auth_type": "api_key", "details": {{"description": "How auth works"}}}}
  ],
  "security_requirements": ["List of security requirements mentioned"],
  "sla_requirements": {{"response_time_ms": null, "availability_percent": null}}
}}

Focus on Indian fintech terms: CIBIL, PAN, Aadhaar, GSTIN, UPI, NEFT, IMPS, eKYC, etc.
Only include fields and endpoints explicitly mentioned or strongly implied.
Return ONLY valid JSON, no markdown fences."""


async def extract_entities_llm(text: str) -> dict[str, Any] | None:
    """Use Gemini to extract structured entities from free-text.

    Returns parsed dict on success, None on failure (caller falls back to regex).
    """
    if not settings.ai_enabled or not settings.gemini_api_key:
        return None

    truncated = text[:15000]

    try:
        client = get_llm_client()
        prompt = _EXTRACTION_PROMPT.format(text=truncated)

        result = await client.generate_json(
            prompt,
            system_instruction=_SYSTEM_INSTRUCTION,
            temperature=0.1,
            max_tokens=4096,
        )

        entity_count = (
            len(result.get("endpoints", []))
            + len(result.get("fields", []))
            + len(result.get("auth_requirements", []))
            + len(result.get("services_identified", []))
        )
        logger.info("llm_entity_extraction_succeeded entities=%d", entity_count)
        return result

    except Exception:
        logger.warning("llm_entity_extraction_failed", exc_info=True)
        return None
