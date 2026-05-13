"""FastMCP server that exposes :class:`BridgeService` over stdio.

The server is built lazily by :func:`build_server` so tests can construct a
fresh, isolated instance. The three tool functions are thin shims over the
bridge; all business logic lives in :mod:`finspark.mcp.service`.
"""
from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from finspark.mcp.service import BridgeService

logger = logging.getLogger(__name__)

SERVER_NAME = "adaptconfig-mcp"
SERVER_INSTRUCTIONS = (
    "AdaptConfig MCP bridge. Use list_adapters to inspect the catalogue, "
    "generate_config to turn a document into a runnable configuration, and "
    "invoke to run that configuration through the chain executor against the "
    "mock-response store."
)

TOOL_NAMES = ("list_adapters", "generate_config", "invoke")


def build_server(bridge: BridgeService | None = None) -> FastMCP:
    """Return a configured :class:`FastMCP` ready to run on stdio.

    A caller-supplied ``bridge`` lets tests inject a stub. When omitted, a
    default :class:`BridgeService` is constructed lazily.
    """
    service = bridge or BridgeService()
    server = FastMCP(SERVER_NAME, instructions=SERVER_INSTRUCTIONS)

    @server.tool(
        name="list_adapters",
        description=(
            "Return the adapter catalogue: total count, distinct categories, "
            "and per-adapter version metadata (auth_type, base_url, endpoints)."
        ),
    )
    async def list_adapters() -> dict[str, Any]:
        return await service.list_adapters_summary()

    @server.tool(
        name="generate_config",
        description=(
            "Parse a free-text integration document and persist an AdaptConfig "
            "configuration. Returns the new config_id and the adapter that was "
            "matched. Pass adapter_hint (name or category) to override the "
            "automatic catalogue match."
        ),
    )
    async def generate_config(
        document_text: str,
        adapter_hint: str | None = None,
    ) -> dict[str, Any]:
        return await service.generate_config_from_text(document_text, adapter_hint)

    @server.tool(
        name="invoke",
        description=(
            "Execute the chain stored at config_id against the simulator's "
            "mock-response store. Returns the simulation summary plus a "
            "final_response convenience field with the last endpoint's mock "
            "output. The payload dict overrides the sample request the "
            "simulator builds from field_mappings."
        ),
    )
    async def invoke(
        config_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await service.invoke_config(config_id, payload)

    return server


__all__ = ["SERVER_NAME", "SERVER_INSTRUCTIONS", "TOOL_NAMES", "build_server"]
