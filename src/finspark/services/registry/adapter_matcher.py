"""LLM-driven adapter matching for parsed documents.

Given a parsed document and the seeded adapter catalogue, rank the top-3
best-fit adapters with a 0-1 confidence score and a one-sentence reason.

Falls back to a deterministic keyword overlap heuristic when the LLM call
raises (network error, JSON parse error, or empty catalogue) so the endpoint
stays usable in degraded conditions.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from finspark.models.adapter import Adapter
from finspark.services.llm.client import LLMAPIError

logger = logging.getLogger(__name__)

# Confidence threshold below which we tell the UI to offer "Create custom
# adapter" instead of trying to reuse an existing one.
SUGGEST_CUSTOM_THRESHOLD = 0.55

# Compact token budget — ranking JSON for 3 matches comfortably fits, and
# gpt-4.1-nano is cheap enough that we never want to over-allocate here.
_MAX_TOKENS = 1024
_TEMPERATURE = 0.0
_TOP_N = 3

_SYSTEM_INSTRUCTION = (
    "You are an integration architect for Indian fintech APIs. "
    "Given a parsed API spec and a catalogue of pre-built adapters, rank the "
    "top matches by how well each adapter could serve the spec's endpoints "
    "and fields. Only return valid JSON in the requested shape."
)


def _summarise_adapter(adapter: Adapter) -> dict[str, Any]:
    """Build a compact adapter card for the LLM prompt.

    Includes one entry per version so the model can tell us which version
    fits the document best.
    """
    versions: list[dict[str, Any]] = []
    for v in adapter.versions:
        endpoint_paths: list[str] = []
        if v.endpoints:
            try:
                raw = json.loads(v.endpoints)
                if isinstance(raw, list):
                    endpoint_paths = [
                        str(ep.get("path", ""))
                        for ep in raw
                        if isinstance(ep, dict) and ep.get("path")
                    ]
            except (json.JSONDecodeError, TypeError, ValueError):
                endpoint_paths = []
        versions.append(
            {
                "version_id": v.id,
                "version": v.version,
                "auth_type": v.auth_type,
                "endpoints": endpoint_paths[:8],
            }
        )
    return {
        "adapter_id": adapter.id,
        "name": adapter.name,
        "category": adapter.category,
        "description": adapter.description or "",
        "versions": versions,
    }


def _summarise_document(parsed: dict[str, Any]) -> dict[str, Any]:
    """Compact view of a parsed document for the LLM prompt."""
    endpoints = parsed.get("endpoints") or []
    fields = parsed.get("fields") or []
    return {
        "title": parsed.get("title", ""),
        "summary": (parsed.get("summary") or "")[:400],
        "services_identified": parsed.get("services_identified") or [],
        "endpoints": [
            {
                "path": ep.get("path", ""),
                "method": ep.get("method", "GET"),
            }
            for ep in endpoints[:12]
            if isinstance(ep, dict)
        ],
        "fields": [f.get("name", "") for f in fields[:24] if isinstance(f, dict)],
    }


def _build_prompt(doc_summary: dict[str, Any], catalogue: list[dict[str, Any]]) -> str:
    """Assemble the compact ranking prompt."""
    return (
        "Rank up to "
        f"{_TOP_N} adapters from the catalogue that best match the document. "
        "Score each match 0.0-1.0 (1.0 = perfect overlap of endpoints and fields, "
        "0.0 = unrelated). Use the version_id of the SINGLE most relevant version "
        "for each adapter. Return valid JSON ONLY with this shape:\n"
        '{"matches": [{"adapter_id": "...", "version_id": "...", '
        '"score": 0.0, "reason": "..."}]}'
        "\n\nDocument:\n"
        f"{json.dumps(doc_summary, ensure_ascii=False)}"
        "\n\nAdapter catalogue:\n"
        f"{json.dumps(catalogue, ensure_ascii=False)}"
    )


def _heuristic_score(doc_summary: dict[str, Any], adapter_summary: dict[str, Any]) -> tuple[float, str]:
    """Deterministic fallback when the LLM is unavailable.

    Cheap keyword overlap between the document's title/services/fields and
    the adapter's name/category/description. Used so the endpoint still
    returns SOMETHING useful when OpenAI is unreachable in CI or dev.
    """
    haystack_parts: list[str] = [
        adapter_summary.get("name", ""),
        adapter_summary.get("category", ""),
        adapter_summary.get("description", ""),
    ]
    haystack = " ".join(haystack_parts).lower()
    needles: set[str] = set()
    if doc_summary.get("title"):
        needles.update(str(doc_summary["title"]).lower().split())
    for svc in doc_summary.get("services_identified") or []:
        needles.update(str(svc).lower().split())
    for fname in doc_summary.get("fields") or []:
        needles.add(str(fname).lower())
    for ep in doc_summary.get("endpoints") or []:
        path = str(ep.get("path", ""))
        for tok in path.replace("/", " ").replace("-", " ").replace("_", " ").split():
            if tok:
                needles.add(tok.lower())
    if not needles:
        return 0.0, "No content to match against."
    hits = sum(1 for n in needles if len(n) > 2 and n in haystack)
    score = min(1.0, hits / max(len(needles), 1) * 2.0)
    return round(score, 2), f"Heuristic match: {hits} overlapping terms."


async def suggest_adapters(
    parsed: dict[str, Any],
    adapters: list[Adapter],
    *,
    llm_client: Any,
    top_n: int = _TOP_N,
) -> tuple[list[dict[str, Any]], bool]:
    """Return ``(ranked_matches, suggest_custom)``.

    ``ranked_matches`` is a list of dicts shaped like ``AdapterMatch`` (without
    Pydantic validation — the route layer wraps them) sorted by ``score`` desc.
    ``suggest_custom`` is True when the best score is below
    :data:`SUGGEST_CUSTOM_THRESHOLD`.
    """
    if not adapters:
        return [], True

    doc_summary = _summarise_document(parsed)
    catalogue = [_summarise_adapter(a) for a in adapters]
    adapter_by_id = {a.id: a for a in adapters}
    version_lookup: dict[tuple[str, str], tuple[str, str]] = {}
    for adapter in adapters:
        for v in adapter.versions:
            version_lookup[(adapter.id, v.id)] = (adapter.name, v.version)

    matches: list[dict[str, Any]] = []
    llm_failed = False

    try:
        raw = await llm_client.generate_json(
            _build_prompt(doc_summary, catalogue),
            system_instruction=_SYSTEM_INSTRUCTION,
            temperature=_TEMPERATURE,
            max_tokens=_MAX_TOKENS,
        )
    except LLMAPIError as exc:  # network, parse, or shape error
        logger.warning("adapter_suggest_llm_error error=%s", exc)
        llm_failed = True
        raw = {"matches": []}
    except Exception as exc:  # noqa: BLE001 — last-resort fallback
        logger.warning("adapter_suggest_unexpected_error error=%s", exc)
        llm_failed = True
        raw = {"matches": []}

    candidate_list = raw.get("matches") if isinstance(raw, dict) else None
    if not isinstance(candidate_list, list):
        candidate_list = []

    for item in candidate_list:
        if not isinstance(item, dict):
            continue
        adapter_id = str(item.get("adapter_id", ""))
        version_id = str(item.get("version_id", ""))
        if not adapter_id or adapter_id not in adapter_by_id:
            continue
        key = (adapter_id, version_id)
        if key not in version_lookup:
            # LLM hallucinated a version_id — pin to the first version of the
            # adapter so the suggestion is still actionable.
            adapter = adapter_by_id[adapter_id]
            if not adapter.versions:
                continue
            version_id = adapter.versions[0].id
            key = (adapter_id, version_id)
        try:
            score = float(item.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(1.0, score))
        adapter_name, version_label = version_lookup[key]
        matches.append(
            {
                "adapter_id": adapter_id,
                "version_id": version_id,
                "adapter_name": adapter_name,
                "version": version_label,
                "score": round(score, 2),
                "reason": str(item.get("reason", ""))[:240],
            }
        )

    if llm_failed and not matches:
        # Score every adapter heuristically — degraded but deterministic.
        for adapter in adapters:
            adapter_summary = _summarise_adapter(adapter)
            score, reason = _heuristic_score(doc_summary, adapter_summary)
            if not adapter.versions:
                continue
            version = adapter.versions[0]
            matches.append(
                {
                    "adapter_id": adapter.id,
                    "version_id": version.id,
                    "adapter_name": adapter.name,
                    "version": version.version,
                    "score": score,
                    "reason": reason,
                }
            )

    matches.sort(key=lambda m: m["score"], reverse=True)
    matches = matches[:top_n]

    top_score = matches[0]["score"] if matches else 0.0
    suggest_custom = top_score < SUGGEST_CUSTOM_THRESHOLD
    return matches, suggest_custom
