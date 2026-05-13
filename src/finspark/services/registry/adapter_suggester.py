"""Adapter-suggestion service.

Given a parsed document and the active adapter catalogue, ranks the top-3
adapter/version pairs by fit. The ranking is produced by a single
constrained-JSON LLM call (OpenAI-preferred via :func:`get_llm_client`)
with a deterministic keyword-overlap fallback when the LLM response is
unusable.

Strict scope:
* No vector embeddings.
* No DB writes.
* No edits to the catalogue.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from finspark.core.json_utils import safe_json_loads
from finspark.schemas.adapters import AdapterSuggestMatch, AdapterSuggestResponse
from finspark.schemas.common import AdapterCategory
from finspark.services.llm.client import GeminiAPIError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from finspark.models.adapter import Adapter

logger = logging.getLogger(__name__)


SUGGEST_THRESHOLD = 0.55
TOP_N = 3
LLM_MAX_TOKENS = 1024
LLM_TEMPERATURE = 0.0
DOC_PAYLOAD_CHAR_LIMIT = 1500


_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def _tokens(*chunks: str | None) -> set[str]:
    """Lower-case tokens of length >= 3 across the given strings."""
    out: set[str] = set()
    for chunk in chunks:
        if not chunk:
            continue
        out.update(_TOKEN_RE.findall(chunk.lower()))
    return out


def _coerce_score(raw: Any) -> float:
    """Best-effort coerce arbitrary input to a [0, 1] float."""
    try:
        score = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


class AdapterSuggester:
    """Rank adapters against a parsed document with LLM + keyword fallback."""

    threshold: float = SUGGEST_THRESHOLD

    def __init__(self, llm_client: Any | None = None) -> None:
        # ``llm_client`` is expected to expose ``generate_json`` per the
        # ``OpenAIClient`` / ``GeminiClient`` surface. Pass ``None`` to
        # force the deterministic fallback path (used by unit tests).
        self.llm_client = llm_client

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    async def suggest(
        self,
        parsed_result: dict[str, Any],
        adapters: list[Adapter],
    ) -> AdapterSuggestResponse:
        """Return ranked top-3 matches and ``suggest_custom`` flag."""
        catalogue = self._build_catalogue(adapters)
        if not catalogue:
            return AdapterSuggestResponse(
                matches=[], suggest_custom=True, threshold=self.threshold
            )

        ranked = await self._rank_via_llm(parsed_result, catalogue)
        if not ranked:
            ranked = self._rank_via_keywords(parsed_result, catalogue)

        ranked.sort(key=lambda m: m.score, reverse=True)
        ranked = ranked[:TOP_N]

        suggest_custom = (not ranked) or ranked[0].score < self.threshold
        return AdapterSuggestResponse(
            matches=ranked,
            suggest_custom=suggest_custom,
            threshold=self.threshold,
        )

    # ------------------------------------------------------------------
    # Catalogue + payload prep
    # ------------------------------------------------------------------
    @staticmethod
    def _build_catalogue(adapters: Iterable[Adapter]) -> list[dict[str, Any]]:
        catalogue: list[dict[str, Any]] = []
        for adapter in adapters:
            versions: list[dict[str, Any]] = []
            for version in adapter.versions:
                paths: list[str] = []
                if version.endpoints:
                    loaded = safe_json_loads(version.endpoints, [])
                    if isinstance(loaded, list):
                        paths = [
                            str(ep.get("path", ""))
                            for ep in loaded
                            if isinstance(ep, dict) and ep.get("path")
                        ]
                versions.append(
                    {
                        "version_id": version.id,
                        "version": version.version,
                        "endpoints": paths,
                    }
                )
            if not versions:
                continue
            catalogue.append(
                {
                    "adapter_id": adapter.id,
                    "name": adapter.name,
                    "category": adapter.category,
                    "description": adapter.description or "",
                    "versions": versions,
                }
            )
        return catalogue

    @staticmethod
    def _summarise_document(parsed_result: dict[str, Any]) -> dict[str, Any]:
        endpoints = parsed_result.get("endpoints") or []
        endpoint_paths = [
            str(ep.get("path", ""))
            for ep in endpoints
            if isinstance(ep, dict) and ep.get("path")
        ]
        fields = parsed_result.get("fields") or []
        field_names = [
            str(f.get("name", ""))
            for f in fields
            if isinstance(f, dict) and f.get("name")
        ][:10]
        sections = parsed_result.get("sections") or {}
        base_url = ""
        if isinstance(sections, dict):
            base_url = str(sections.get("base_urls") or sections.get("base_url") or "")

        summary: dict[str, Any] = {
            "title": str(parsed_result.get("title") or ""),
            "summary": str(parsed_result.get("summary") or ""),
            "services_identified": list(parsed_result.get("services_identified") or [])[:10],
            "endpoint_paths": endpoint_paths[:15],
            "field_names": field_names,
            "base_url": base_url,
        }

        encoded = json.dumps(summary, ensure_ascii=False)
        if len(encoded) > DOC_PAYLOAD_CHAR_LIMIT:
            summary["summary"] = summary["summary"][: max(0, DOC_PAYLOAD_CHAR_LIMIT // 4)]
            summary["endpoint_paths"] = summary["endpoint_paths"][:5]
            summary["field_names"] = summary["field_names"][:5]
        return summary

    # ------------------------------------------------------------------
    # LLM ranking
    # ------------------------------------------------------------------
    async def _rank_via_llm(
        self,
        parsed_result: dict[str, Any],
        catalogue: list[dict[str, Any]],
    ) -> list[AdapterSuggestMatch]:
        if self.llm_client is None:
            return []

        doc_payload = self._summarise_document(parsed_result)
        catalogue_payload = [
            {
                "adapter_id": a["adapter_id"],
                "name": a["name"],
                "category": a["category"],
                "description": a["description"],
                "versions": [
                    {
                        "version_id": v["version_id"],
                        "version": v["version"],
                        "endpoints": v["endpoints"][:8],
                    }
                    for v in a["versions"]
                ],
            }
            for a in catalogue
        ]

        system_instruction = (
            "You are an Indian fintech integration assistant. "
            "Given a parsed API/BRD document and a catalogue of pre-built "
            "adapters, rank up to 3 adapter/version pairs by best fit. "
            "Respond with strict JSON only, no prose, matching the schema:\n"
            "{\"matches\": [{\"adapter_id\": str, \"version_id\": str, "
            "\"score\": number 0-1, \"reason\": str <= 200 chars}]}\n"
            "Use ONLY adapter_id and version_id values that appear in the "
            "catalogue. Do not invent IDs. If nothing fits, return "
            "{\"matches\": []}."
        )
        prompt = json.dumps(
            {"document": doc_payload, "catalogue": catalogue_payload},
            ensure_ascii=False,
        )

        try:
            data = await self.llm_client.generate_json(
                prompt,
                system_instruction=system_instruction,
                temperature=LLM_TEMPERATURE,
                max_tokens=LLM_MAX_TOKENS,
            )
        except GeminiAPIError as exc:
            logger.warning("adapter_suggester_llm_error error=%s", exc)
            return []
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning("adapter_suggester_llm_unexpected error=%s", exc)
            return []

        return self._validate_llm_matches(data, catalogue)

    @staticmethod
    def _validate_llm_matches(
        data: Any,
        catalogue: list[dict[str, Any]],
    ) -> list[AdapterSuggestMatch]:
        if not isinstance(data, dict):
            return []
        raw_matches = data.get("matches")
        if not isinstance(raw_matches, list):
            return []

        index: dict[tuple[str, str], dict[str, Any]] = {}
        for adapter in catalogue:
            for version in adapter["versions"]:
                index[(adapter["adapter_id"], version["version_id"])] = {
                    "adapter": adapter,
                    "version": version,
                }

        out: list[AdapterSuggestMatch] = []
        seen: set[tuple[str, str]] = set()
        for entry in raw_matches:
            if not isinstance(entry, dict):
                continue
            adapter_id = str(entry.get("adapter_id") or "")
            version_id = str(entry.get("version_id") or "")
            key = (adapter_id, version_id)
            if not adapter_id or not version_id or key in seen:
                continue
            ctx = index.get(key)
            if ctx is None:
                continue
            seen.add(key)
            score = _coerce_score(entry.get("score"))
            reason = str(entry.get("reason") or "").strip()[:200]
            out.append(
                AdapterSuggestMatch(
                    adapter_id=adapter_id,
                    version_id=version_id,
                    adapter_name=ctx["adapter"]["name"],
                    version=ctx["version"]["version"],
                    category=AdapterCategory(ctx["adapter"]["category"]),
                    score=score,
                    reason=reason,
                )
            )
        return out

    # ------------------------------------------------------------------
    # Deterministic keyword fallback (no embeddings, MVP)
    # ------------------------------------------------------------------
    @staticmethod
    def _rank_via_keywords(
        parsed_result: dict[str, Any],
        catalogue: list[dict[str, Any]],
    ) -> list[AdapterSuggestMatch]:
        sections = parsed_result.get("sections") or {}
        section_text = ""
        if isinstance(sections, dict):
            section_text = " ".join(str(v) for v in sections.values())
        endpoint_paths = " ".join(
            str(ep.get("path", ""))
            for ep in (parsed_result.get("endpoints") or [])
            if isinstance(ep, dict)
        )
        services = " ".join(parsed_result.get("services_identified") or [])
        doc_tokens = _tokens(
            parsed_result.get("title"),
            parsed_result.get("summary"),
            services,
            endpoint_paths,
            section_text,
        )
        if not doc_tokens:
            return []

        out: list[AdapterSuggestMatch] = []
        for adapter in catalogue:
            adapter_endpoint_paths = " ".join(
                p for v in adapter["versions"] for p in v["endpoints"]
            )
            adapter_tokens = _tokens(
                adapter["name"],
                adapter["category"],
                adapter["description"],
                adapter_endpoint_paths,
            )
            if not adapter_tokens:
                continue
            overlap = doc_tokens & adapter_tokens
            denom = len(doc_tokens | adapter_tokens) or 1
            score = round(len(overlap) / denom, 4)
            best_version = adapter["versions"][-1]
            out.append(
                AdapterSuggestMatch(
                    adapter_id=adapter["adapter_id"],
                    version_id=best_version["version_id"],
                    adapter_name=adapter["name"],
                    version=best_version["version"],
                    category=AdapterCategory(adapter["category"]),
                    score=score,
                    reason=(
                        f"keyword overlap {len(overlap)}/{denom} "
                        f"({sorted(overlap)[:5]})"
                    )[:200],
                )
            )
        return out
