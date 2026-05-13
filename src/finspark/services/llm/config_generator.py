"""LLM-powered integration config generation using Gemini.

Takes adapter info + document entities and produces a draft config via
structured JSON generation.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from finspark.services.llm.client import GeminiClient

logger = logging.getLogger(__name__)

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

## Parsed Document Content
{document_content}

## User Hints
{user_hints}

## Output Schema
Return a JSON object with these fields:
{{
  "base_url": "string - the API base URL",
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
      "source_field": "string",
      "target_field": "string",
      "transformation": "string|null",
      "confidence": 0.9
    }}
  ],
  "headers": {{}},
  "error_handling": {{
    "envelope_schema": "string - canonical error response schema name (e.g. ErrorEnvelope)",
    "on_4xx": "string - strategy for 4xx (e.g. surface_to_caller_no_retry)",
    "on_5xx": "string - strategy for 5xx (e.g. retry_with_backoff_then_circuit_break)",
    "circuit_breaker": {{
      "failure_threshold": "integer",
      "recovery_timeout_seconds": "integer"
    }},
    "fallback_strategy": "string - e.g. queue_for_manual_review",
    "retryable_status_codes": "array<integer> - status codes to retry"
  }},
  "hooks": [
    {{
      "event": "string - e.g. on_request, on_response, on_error, on_retry",
      "action": "string - e.g. log_masked, refresh_token, emit_metric, dead_letter",
      "config": {{}}
    }}
  ],
  "security": {{
    "tls_version_minimum": "string - e.g. 1.2 or 1.3",
    "pii_masking": {{}},
    "credential_storage": "string - e.g. encrypted_vault_only",
    "audit_logging": "string - e.g. all_requests_and_responses"
  }},
  "notes": "string - any caveats or assumptions made"
}}

Populate every section. Use values from the parsed document where present;
otherwise infer secure defaults appropriate for the adapter type
(API key/OAuth2/Bearer). Always include at least one hook for on_error
(e.g. retry_with_backoff or alert) and one for on_request (e.g. log_masked).
"""


async def generate_config_llm(
    *,
    adapter_info: dict[str, Any],
    document_content: dict[str, Any],
    user_hint: str = "",
    client: GeminiClient,
) -> dict[str, Any]:
    """Generate an integration config payload using Gemini.

    Parameters
    ----------
    adapter_info : dict
        Adapter metadata (name, version, base_url, auth_type, endpoints).
    document_content : dict
        Parsed document data (fields, endpoints, requirements).
    user_hint : str
        Optional free-text guidance from the user.
    client : GeminiClient
        Gemini client instance.

    Returns
    -------
    dict
        The generated config payload.
    """
    prompt = _GENERATE_PROMPT_TEMPLATE.format(
        adapter_info=json.dumps(adapter_info, indent=2),
        document_content=json.dumps(document_content, indent=2),
        user_hints=user_hint or "(none)",
    )

    logger.info("llm_config_generation_start adapter=%s", adapter_info.get("name", "unknown"))

    result = await client.generate_json(
        prompt,
        system_instruction=_SYSTEM_INSTRUCTION,
        temperature=0.1,
        max_tokens=4096,
    )

    logger.info(
        "llm_config_generation_complete adapter=%s keys=%s",
        adapter_info.get("name", "unknown"),
        list(result.keys()),
    )

    return result
