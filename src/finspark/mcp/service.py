"""Service bridge that powers the MCP tools.

The bridge intentionally reuses the same service classes that the FastAPI
routes depend on (``DocumentParser``, ``ConfigGenerator``,
``IntegrationSimulator``) instead of going through HTTP. Each public method
opens a short-lived DB session via :data:`async_session_factory` so the
methods are safe to call concurrently from the MCP runtime.

The MCP layer is a single-tenant integration point -- a deployment of
``adaptconfig-mcp`` belongs to one agent/IDE -- so all rows are written under
the synthetic tenant id :data:`MCP_TENANT_ID`. A future RBAC follow-up can
replace this with per-token tenancy.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from finspark.core.config import settings
from finspark.core.database import async_session_factory
from finspark.core.json_utils import safe_json_loads
from finspark.models.adapter import Adapter, AdapterVersion
from finspark.models.configuration import Configuration, ConfigurationHistory
from finspark.models.document import Document
from finspark.services.config_engine.field_mapper import ConfigGenerator
from finspark.services.llm.client import GeminiAPIError, get_llm_client
from finspark.services.llm.config_generator import generate_config_llm
from finspark.services.parsing.document_parser import DocumentParser
from finspark.services.simulation.simulator import IntegrationSimulator

logger = logging.getLogger(__name__)

# Synthetic tenant used for every row the MCP bridge writes. A token-scoped
# tenancy model is intentionally deferred (persona out-of-scope: "full RBAC is
# a follow-up").
MCP_TENANT_ID = "mcp-bridge"
MCP_TENANT_NAME = "AdaptConfig MCP Bridge"

# A score below this on hint-based adapter lookup triggers the catalogue
# fallback (highest-overlap adapter by parsed fields/endpoints).
_HINT_FUZZY_THRESHOLD = 0.4


class BridgeError(Exception):
    """Raised when an MCP tool cannot satisfy a request."""


class BridgeService:
    """Direct-call bridge between the MCP runtime and AdaptConfig services."""

    def __init__(
        self,
        *,
        document_parser: DocumentParser | None = None,
        config_generator: ConfigGenerator | None = None,
        simulator: IntegrationSimulator | None = None,
    ) -> None:
        self._document_parser = document_parser or DocumentParser()
        self._config_generator = config_generator or ConfigGenerator()
        self._simulator = simulator or IntegrationSimulator()

    # ------------------------------------------------------------------
    # Tool: list_adapters
    # ------------------------------------------------------------------
    async def list_adapters_summary(self) -> dict[str, Any]:
        """Return a compact catalogue summary for the MCP ``list_adapters`` tool.

        Mirrors the data carried over ``GET /api/v1/adapters/`` but trims it to
        what an agent needs to pick an adapter (name, category, description,
        endpoint paths per version).
        """
        async with async_session_factory() as session:
            stmt = (
                select(Adapter)
                .options(selectinload(Adapter.versions))
                .where(Adapter.is_active.is_(True))
            )
            result = await session.execute(stmt)
            adapters = list(result.scalars().all())

            categories_stmt = select(Adapter.category).distinct()
            categories_result = await session.execute(categories_stmt)
            categories = sorted({row[0] for row in categories_result.all() if row[0]})

        items: list[dict[str, Any]] = []
        for adapter in adapters:
            versions: list[dict[str, Any]] = []
            for version in adapter.versions:
                endpoints = safe_json_loads(version.endpoints, [])
                versions.append(
                    {
                        "id": version.id,
                        "version": version.version,
                        "status": version.status,
                        "auth_type": version.auth_type,
                        "base_url": version.base_url or "",
                        "endpoints": [
                            {
                                "path": ep.get("path", ""),
                                "method": ep.get("method", "GET"),
                                "description": ep.get("description", ""),
                            }
                            for ep in endpoints
                        ],
                    }
                )

            items.append(
                {
                    "id": adapter.id,
                    "name": adapter.name,
                    "category": adapter.category,
                    "description": adapter.description or "",
                    "versions": versions,
                }
            )

        return {
            "total": len(items),
            "categories": categories,
            "adapters": items,
        }

    # ------------------------------------------------------------------
    # Tool: generate_config
    # ------------------------------------------------------------------
    async def generate_config_from_text(
        self,
        document_text: str,
        adapter_hint: str | None = None,
    ) -> dict[str, Any]:
        """Parse ``document_text``, pick an adapter, persist a Configuration.

        The pipeline matches ``POST /api/v1/configurations/generate`` -- LLM
        generation when AI is enabled, rule-based augmentation always applied,
        rule-based fallback on LLM error -- but it also drives the parsing
        step the HTTP route relies on the documents/upload route to do.
        """
        if not document_text or not document_text.strip():
            raise BridgeError("document_text must not be empty")

        parsed_result = await self._parse_document_text(document_text)

        async with async_session_factory() as session:
            adapter_version, adapter = await self._select_adapter_version(
                session, parsed_result, adapter_hint
            )

            av_dict = self._adapter_version_to_dict(adapter, adapter_version)
            config, generation_path = await self._build_config(parsed_result, av_dict)

            # Persist the source document so the configuration has a parent row
            # and so analytics/audit downstream surfaces still work.
            doc_row = Document(
                tenant_id=MCP_TENANT_ID,
                filename="mcp-bridge-input.txt",
                file_type="txt",
                file_size=len(document_text.encode("utf-8")),
                doc_type="brd",
                status="parsed",
                raw_text=document_text[:5000],
                parsed_result=parsed_result.model_dump_json(),
            )
            session.add(doc_row)
            await session.flush()

            configuration = Configuration(
                tenant_id=MCP_TENANT_ID,
                name=f"mcp::{adapter.name} ({adapter_version.version})",
                adapter_version_id=adapter_version.id,
                document_id=doc_row.id,
                status="configured",
                version=1,
                field_mappings=json.dumps(config.get("field_mappings", [])),
                transformation_rules=json.dumps(config.get("transformation_rules", [])),
                hooks=json.dumps(config.get("hooks", [])),
                full_config=json.dumps(config),
            )
            session.add(configuration)
            await session.flush()

            history = ConfigurationHistory(
                tenant_id=MCP_TENANT_ID,
                configuration_id=configuration.id,
                version=1,
                change_type="created",
                new_value=json.dumps(config),
                changed_by=MCP_TENANT_NAME,
            )
            session.add(history)

            config_id = configuration.id
            adapter_name = adapter.name
            adapter_version_label = adapter_version.version
            await session.commit()

        logger.info(
            "mcp_generate_config_complete config_id=%s adapter=%s path=%s",
            config_id,
            adapter_name,
            generation_path,
        )

        return {
            "config_id": config_id,
            "adapter_name": adapter_name,
            "adapter_version": adapter_version_label,
            "generation_path": generation_path,
            "field_mappings": len(config.get("field_mappings", [])),
        }

    # ------------------------------------------------------------------
    # Tool: invoke
    # ------------------------------------------------------------------
    async def invoke_config(
        self,
        config_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the simulator's chain executor for ``config_id``.

        Returns the full simulation result (the same shape the HTTP
        ``/api/v1/simulations/run`` route persists) plus a ``final_response``
        convenience field with the last endpoint's mock response so the agent
        can act on it directly.
        """
        async with async_session_factory() as session:
            stmt = select(Configuration).where(
                Configuration.id == config_id,
                Configuration.tenant_id == MCP_TENANT_ID,
            )
            result = await session.execute(stmt)
            config_row = result.scalar_one_or_none()
            if not config_row or not config_row.full_config:
                raise BridgeError(f"Configuration {config_id!r} not found")

            full_config = json.loads(config_row.full_config)

        # The payload is treated as agent-supplied overrides on top of the
        # config's field-mapping-derived sample request. Anything the agent
        # supplies wins so it can exercise specific code paths.
        overrides = dict(payload or {})
        merged_config = dict(full_config)
        if overrides:
            merged_config = self._inject_payload(merged_config, overrides)

        steps = await asyncio.to_thread(
            self._simulator.run_simulation, merged_config, "full"
        )

        total = len(steps)
        passed = sum(1 for s in steps if s.status == "passed")
        failed = total - passed
        duration_ms = sum(s.duration_ms for s in steps)

        final_response: dict[str, Any] = {}
        for step in reversed(steps):
            if step.step_name.startswith("endpoint_test_") and step.actual_response:
                final_response = step.actual_response
                break

        return {
            "config_id": config_id,
            "status": "passed" if failed == 0 else "failed",
            "total_tests": total,
            "passed_tests": passed,
            "failed_tests": failed,
            "duration_ms": duration_ms,
            "final_response": final_response,
            "steps": [s.model_dump() for s in steps],
            "payload_applied": overrides,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _parse_document_text(self, document_text: str) -> Any:
        """Parse the document with the LLM path when enabled, regex otherwise."""
        use_llm = settings.ai_enabled and (
            bool(settings.openai_api_key) or bool(settings.gemini_api_key)
        )
        if use_llm:
            try:
                client = get_llm_client()
                return await self._document_parser.parse_with_llm(
                    document_text, "mcp-bridge-input.txt", client
                )
            except (GeminiAPIError, Exception) as exc:  # noqa: BLE001
                logger.warning(
                    "mcp_parse_with_llm_failed falling_back_to_regex error=%s", exc
                )
        return self._document_parser.parse_text(document_text)

    async def _select_adapter_version(
        self,
        session: Any,
        parsed_result: Any,
        adapter_hint: str | None,
    ) -> tuple[AdapterVersion, Adapter]:
        """Return ``(version, adapter)`` for ``adapter_hint`` or best fuzzy match.

        ``adapter_hint`` matches against adapter name or category (case
        insensitive substring). When no hint -- or the hint misses -- the
        adapter with the highest endpoint/field overlap against
        ``parsed_result`` is chosen. Raises :class:`BridgeError` if the
        catalogue is empty.
        """
        stmt = (
            select(Adapter)
            .options(selectinload(Adapter.versions))
            .where(Adapter.is_active.is_(True))
        )
        result = await session.execute(stmt)
        adapters: list[Adapter] = list(result.scalars().all())
        if not adapters:
            raise BridgeError(
                "No active adapters available -- seed the catalogue first"
            )

        if adapter_hint:
            hint = adapter_hint.strip().lower()
            for adapter in adapters:
                haystack = " ".join(
                    [adapter.name or "", adapter.category or "", adapter.description or ""]
                ).lower()
                if hint in haystack:
                    version = self._latest_active_version(adapter)
                    if version is not None:
                        return version, adapter

        scored = self._score_adapters(adapters, parsed_result)
        top_adapter = scored[0]
        version = self._latest_active_version(top_adapter)
        if version is None:
            # No active version on the top scorer -- scan down the list.
            for adapter in scored:
                version = self._latest_active_version(adapter)
                if version is not None:
                    return version, adapter
            raise BridgeError("No active adapter versions found in catalogue")
        return version, top_adapter

    @staticmethod
    def _latest_active_version(adapter: Adapter) -> AdapterVersion | None:
        active = [v for v in adapter.versions if v.status == "active"]
        if not active:
            return None
        return max(active, key=lambda v: (v.version_order, v.version))

    @staticmethod
    def _score_adapters(adapters: list[Adapter], parsed_result: Any) -> list[Adapter]:
        """Return adapters ordered by overlap with ``parsed_result``."""
        doc_endpoints = {ep.path for ep in getattr(parsed_result, "endpoints", [])}
        doc_fields = {f.name for f in getattr(parsed_result, "fields", [])}
        services = {s.lower() for s in getattr(parsed_result, "services_identified", [])}

        scored: list[tuple[float, Adapter]] = []
        for adapter in adapters:
            endpoint_overlap = 0.0
            field_overlap = 0.0
            for version in adapter.versions:
                eps = {
                    ep.get("path", "")
                    for ep in safe_json_loads(version.endpoints, [])
                }
                req_schema = safe_json_loads(version.request_schema, {})
                props = set((req_schema or {}).get("properties", {}).keys())
                if eps and doc_endpoints:
                    endpoint_overlap = max(
                        endpoint_overlap,
                        len(doc_endpoints & eps) / len(doc_endpoints | eps),
                    )
                if props and doc_fields:
                    field_overlap = max(
                        field_overlap,
                        len(doc_fields & props) / len(doc_fields | props),
                    )

            category_bonus = 0.1 if adapter.category and adapter.category.lower() in services else 0.0
            name_bonus = 0.1 if adapter.name and adapter.name.lower() in " ".join(services) else 0.0
            score = endpoint_overlap * 0.6 + field_overlap * 0.4 + category_bonus + name_bonus
            scored.append((score, adapter))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [adapter for _score, adapter in scored]

    @staticmethod
    def _adapter_version_to_dict(adapter: Adapter, version: AdapterVersion) -> dict[str, Any]:
        return {
            "adapter_name": adapter.name,
            "version": version.version,
            "base_url": version.base_url or "",
            "auth_type": version.auth_type,
            "endpoints": safe_json_loads(version.endpoints, []),
            "request_schema": safe_json_loads(version.request_schema, {}),
            "response_schema": safe_json_loads(version.response_schema, {}),
        }

    async def _build_config(
        self, parsed_result: Any, av_dict: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        """Build the integration config dict, mirroring the HTTP route."""
        parsed_dict = parsed_result.model_dump() if hasattr(parsed_result, "model_dump") else dict(parsed_result)

        use_llm = settings.ai_enabled and (
            bool(settings.openai_api_key) or bool(settings.gemini_api_key)
        )
        generation_path = "rule_based"
        config: dict[str, Any] = {}

        if use_llm:
            try:
                client = get_llm_client()
                adapter_info = {
                    "name": av_dict["adapter_name"],
                    "version": av_dict["version"],
                    "base_url": av_dict["base_url"],
                    "auth_type": av_dict["auth_type"],
                }
                llm_config = await generate_config_llm(
                    adapter_info=adapter_info,
                    document_content=parsed_dict,
                    client=client,
                )
                config = self._merge_rule_based(llm_config, parsed_dict, av_dict)
                generation_path = "llm_with_rule_augment"
            except (GeminiAPIError, Exception) as exc:  # noqa: BLE001
                logger.warning(
                    "mcp_llm_generation_failed_falling_back error=%s adapter=%s",
                    exc,
                    av_dict.get("adapter_name", "unknown"),
                )
                config = await asyncio.to_thread(
                    self._config_generator.generate, parsed_dict, av_dict
                )
                generation_path = "rule_based_fallback"
        else:
            config = await asyncio.to_thread(
                self._config_generator.generate, parsed_dict, av_dict
            )

        config["_generation_path"] = generation_path
        config.setdefault("adapter_name", av_dict.get("adapter_name", ""))
        config.setdefault("version", av_dict.get("version", "v1"))
        config.setdefault("base_url", av_dict.get("base_url", ""))
        config.setdefault(
            "auth",
            {"type": av_dict.get("auth_type", "api_key"), "credentials": {}},
        )

        auth_config = config.get("auth", {})
        if not auth_config.get("credentials"):
            auth_config["credentials"] = {
                "api_key": "env:ADAPTER_API_KEY",
                "api_secret": "env:ADAPTER_API_SECRET",
            }
            config["auth"] = auth_config

        # Drop unresolved mappings + collapse duplicate targets so the
        # persisted field_mappings only contain actionable entries.
        raw_mappings = config.get("field_mappings", [])
        for fm in raw_mappings:
            if fm.get("target_field") and fm.get("confidence", 0) < 0.5:
                fm["confidence"] = max(fm.get("confidence", 0), 0.6)
        by_target: dict[str, dict[str, Any]] = {}
        for mapping in raw_mappings:
            tgt = mapping.get("target_field")
            if not tgt or (mapping.get("confidence") or 0) <= 0:
                continue
            existing = by_target.get(tgt)
            if existing is None or (mapping.get("confidence") or 0) > (
                existing.get("confidence") or 0
            ):
                by_target[tgt] = mapping
        config["field_mappings"] = list(by_target.values())

        return config, generation_path

    def _merge_rule_based(
        self,
        llm_config: dict[str, Any],
        parsed_dict: dict[str, Any],
        av_dict: dict[str, Any],
    ) -> dict[str, Any]:
        """Augment ``llm_config`` with rule-based field mapper output.

        Mirrors :func:`finspark.api.routes.configurations._augment_with_rule_based`
        without importing it -- the route module pulls in FastAPI dependencies
        we want to keep out of the MCP path.
        """
        rule_config = self._config_generator.generate(parsed_dict, av_dict)
        rule_mappings: list[dict[str, Any]] = rule_config.get("field_mappings", [])
        existing_mappings: list[dict[str, Any]] = llm_config.get("field_mappings", [])
        rule_by_source = {m.get("source_field", ""): m for m in rule_mappings}

        augmented: list[dict[str, Any]] = []
        seen_sources: set[str] = set()
        for mapping in existing_mappings:
            src = mapping.get("source_field", "")
            seen_sources.add(src)
            merged = dict(mapping)
            rule_m = rule_by_source.get(src)
            if rule_m:
                merged["confidence"] = rule_m.get(
                    "confidence", merged.get("confidence", 0.0)
                )
                merged["is_confirmed"] = rule_m.get(
                    "is_confirmed", merged.get("is_confirmed", False)
                )
            augmented.append(merged)

        for rule_m in rule_mappings:
            src = rule_m.get("source_field", "")
            if src and src not in seen_sources and rule_m.get("target_field"):
                augmented.append(rule_m)

        augmented = [
            m
            for m in augmented
            if m.get("target_field") and (m.get("confidence") or 0) > 0
        ]

        result = dict(llm_config)
        result["field_mappings"] = augmented
        return result

    @staticmethod
    def _inject_payload(
        config: dict[str, Any], overrides: dict[str, Any]
    ) -> dict[str, Any]:
        """Surface caller-supplied values on the field mappings.

        The simulator builds its sample request from ``field_mappings`` and the
        :class:`MockAPIServer`'s ``MOCK_DATA`` dict. Adding the agent overrides
        as source values lets the chain run see the caller's payload without
        rewriting the simulator. The original config is not mutated.
        """
        mappings = list(config.get("field_mappings", []))
        existing = {m.get("source_field", ""): m for m in mappings}
        for key, value in overrides.items():
            if key in existing:
                existing[key] = {
                    **existing[key],
                    "sample_value": value,
                }
            else:
                mappings.append(
                    {
                        "source_field": key,
                        "target_field": key,
                        "confidence": 0.6,
                        "sample_value": value,
                    }
                )
        merged = dict(config)
        merged["field_mappings"] = [existing.get(m.get("source_field", ""), m) for m in mappings]
        return merged
