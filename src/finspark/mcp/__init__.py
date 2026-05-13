"""MCP (Model Context Protocol) bridge for AdaptConfig.

Exposes the integration catalogue and chain executor as a stdio MCP server so
any IDE or agent can drive AdaptConfig as middleware between two services.

The bridge consults the existing config + simulation pipeline and exposes three
tools that mirror the HTTP surface without re-spawning HTTP machinery:

* ``list_adapters`` -- catalogue summary.
* ``generate_config`` -- parse a document and persist a configuration.
* ``invoke`` -- run the chain through the simulator's mock-response store.

See :class:`finspark.mcp.service.BridgeService` for the service-level entry
points and :func:`finspark.mcp.server.build_server` for tool registration.

Related: issue #114 -- Runtime API Proxy.
"""
from __future__ import annotations

from finspark.mcp.server import build_server
from finspark.mcp.service import BridgeService

__all__ = ["BridgeService", "build_server"]
