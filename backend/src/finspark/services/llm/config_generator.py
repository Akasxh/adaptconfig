"""LLM-powered integration config generation using Gemini.

Takes parsed document entities + adapter schema and produces
a draft IntegrationConfig via structured JSON generation.
"""
from __future__ import annotations

import json
from typing import Any

import structlog

from finspark.services.llm.client import GeminiClient, get_llm_client

logger = structlog.get_logger(__name__)

_SYSTEM_INSTRUCTION = """\
You are FinSpark, an AI integration configuration engine for enterprise lending platforms.
Your job is to generate integration configurations by analyzing parsed document entities
(from BRDs, API specs) and mapping them to adapter schemas.

Always respond with valid JSON matching the requested schema. Be precise with field mappings,
timeouts, retry policies, and auth configurations. Use the document entities as the primary
source of truth for values like endpoints, field names, SLAs, and security requirements."""

_GENERATE_PROMPT_TEMPLATE = """\
Generate an integration configuration for the following adapter.

## Adapter Info
{adapter_info}

## Parsed Document Entities
{document_entities}

## User Hints
{user_hints}

## Output Schema
Return a JSON object with these fields:
{{
  "base_url": "string - the API base URL from the documents",
  "endpoints": [
    {{
      "path": "string",
      "method": "GET|POST|PUT|DELETE",
      "description": "string"
    }}
  ],
  "auth": {{
    "type": "api_key|bearer|oauth2|basic",
    "config": {{}}
  }},
  "timeout_ms": "integer - request timeout in milliseconds",
  "retry_count": "integer - max retry attempts",
  "retry_backoff": "linear|exponential",
  "field_mappings": [
    {{
      "source_field": "string - field name in the source system",
      "target_field": "string - field name in the adapter API",
      "transform": "string|null - transformation to apply",
      "required": true
    }}
  ],
  "headers": {{}},
  "notes": "string - any caveats or assumptions made"
}}"""


async def generate_config(
    *,
    adapter_info: dict[str, Any],
    document_entities: list[dict[str, Any]],
    user_hint: str = "",
    client: GeminiClient | None = None,
) -> dict[str, Any]:
    """Generate an integration config payload using Gemini.

    Parameters
    ----------
    adapter_info : dict
        Adapter metadata (name, category, version, schema).
    document_entities : list[dict]
        Extracted entities from parsed BRDs / API specs.
    user_hint : str
        Optional free-text guidance from the user.
    client : GeminiClient | None
        Injected client for testing; uses singleton otherwise.

    Returns
    -------
    dict
        The generated config payload ready to store as ConfigRecord.payload.
    """
    llm = client or get_llm_client()

    prompt = _GENERATE_PROMPT_TEMPLATE.format(
        adapter_info=json.dumps(adapter_info, indent=2),
        document_entities=json.dumps(document_entities, indent=2),
        user_hints=user_hint or "(none)",
    )

    logger.info(
        "llm_config_generation_start",
        adapter=adapter_info.get("name", "unknown"),
    )

    result = await llm.generate_json(
        prompt,
        system_instruction=_SYSTEM_INSTRUCTION,
        temperature=0.1,
        max_tokens=4096,
    )

    logger.info(
        "llm_config_generation_complete",
        adapter=adapter_info.get("name", "unknown"),
        keys=list(result.keys()),
    )

    return result
