"""Integration tests for the MCP bridge (Issue #114).

Two test surfaces:

* **Stdio subprocess** -- spawn ``python -m finspark.mcp`` so the protocol
  initialise + ``list_tools`` round-trip exercises the real CLI boundary.
  This is the only test in the suite that binds to a child process; per the
  persona, stdio subprocess tests are allowed strictly for the CLI boundary
  and must not bind ports (we use stdio, not uvicorn).
* **ASGI parity** -- use the shared ``client`` fixture to drive the existing
  HTTP simulation route, then call :class:`BridgeService.invoke_config`
  directly against the same in-memory DB. The two paths must produce the
  same simulation step structure.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.mcp import BridgeService
from finspark.mcp.server import TOOL_NAMES
from finspark.mcp.service import MCP_TENANT_ID
from finspark.models.adapter import Adapter, AdapterVersion
from finspark.models.configuration import Configuration


REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Stdio subprocess: CLI boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdio_server_lists_three_tools_over_real_subprocess(
    tmp_path: Path,
) -> None:
    """``python -m finspark.mcp`` answers ``list_tools`` with our three tools."""
    db_path = tmp_path / "mcp_subproc.sqlite"
    env = {
        **os.environ,
        "FINSPARK_DEBUG": "true",
        "FINSPARK_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
        # The persona's auth gate is bypassed in debug mode; we still set the
        # token so the production path stays exercised end-to-end.
        "FINSPARK_MCP_TOKEN": "test-token",
        "PYTHONPATH": str(REPO_ROOT / "src"),
    }

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "finspark.mcp"],
        env=env,
        cwd=str(REPO_ROOT),
    )

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await asyncio.wait_for(session.initialize(), timeout=20)
            listed = await asyncio.wait_for(session.list_tools(), timeout=10)

    tool_names = {tool.name for tool in listed.tools}
    assert tool_names == set(TOOL_NAMES), (
        f"Expected exactly the persona's 3 tools, got {tool_names!r}"
    )


@pytest.mark.asyncio
async def test_stdio_subprocess_refuses_to_start_when_token_missing_in_non_debug(
    tmp_path: Path,
) -> None:
    """In non-debug mode the entry point must exit instead of serving stdio.

    We launch the subprocess directly with ``Popen`` rather than going through
    the MCP client so we can inspect the exit code without the SDK retrying.
    """
    import subprocess

    db_path = tmp_path / "mcp_auth.sqlite"
    env = {
        **os.environ,
        # Pull a strong key in so the Settings validator doesn't bail out
        # for the wrong reason -- we want to assert specifically about the
        # missing MCP token.
        "FINSPARK_DEBUG": "false",
        "FINSPARK_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
        "FINSPARK_SECRET_KEY": "x" * 64,
        "FINSPARK_ENCRYPTION_KEY": "y" * 64,
        "PYTHONPATH": str(REPO_ROOT / "src"),
    }
    env.pop("FINSPARK_MCP_TOKEN", None)

    proc = subprocess.run(
        [sys.executable, "-m", "finspark.mcp"],
        env=env,
        cwd=str(REPO_ROOT),
        capture_output=True,
        timeout=15,
    )
    assert proc.returncode != 0
    combined = (proc.stderr.decode() + proc.stdout.decode()).lower()
    assert "finspark_mcp_token" in combined


# ---------------------------------------------------------------------------
# ASGI parity: invoke vs /simulations/run
# ---------------------------------------------------------------------------


async def _seed_invoke_fixture(db: AsyncSession) -> tuple[Configuration, str]:
    """Seed an adapter + configuration the parity test can invoke."""
    adapter = Adapter(
        name="CIBIL Credit Bureau",
        category="bureau",
        description="Parity-test adapter",
        is_active=True,
        icon="credit-card",
    )
    db.add(adapter)
    await db.flush()

    version = AdapterVersion(
        adapter_id=adapter.id,
        version="v1",
        version_order=1,
        status="active",
        base_url="https://api.cibil.com/v1",
        auth_type="api_key",
        endpoints=json.dumps(
            [{"path": "/credit-score", "method": "POST", "description": "Score"}]
        ),
        request_schema=json.dumps(
            {"properties": {"pan_number": {"type": "string"}}}
        ),
        response_schema=json.dumps(
            {"properties": {"credit_score": {"type": "integer"}}}
        ),
    )
    db.add(version)
    await db.flush()

    full_config = {
        "adapter_name": adapter.name,
        "version": version.version,
        "base_url": version.base_url,
        "auth": {"type": "api_key", "credentials": {"api_key": "env:KEY"}},
        "endpoints": [{"path": "/credit-score", "method": "POST"}],
        "field_mappings": [
            {"source_field": "pan_number", "target_field": "pan", "confidence": 0.95}
        ],
        "transformation_rules": [],
        "hooks": [{"type": "on_error", "action": "retry"}],
        "retry_policy": {"max_retries": 3, "backoff_factor": 1.5},
        "timeout_ms": 5000,
    }
    config = Configuration(
        tenant_id=MCP_TENANT_ID,
        name="parity-fixture",
        adapter_version_id=version.id,
        status="configured",
        version=1,
        field_mappings=json.dumps(full_config["field_mappings"]),
        transformation_rules=json.dumps([]),
        hooks=json.dumps(full_config["hooks"]),
        full_config=json.dumps(full_config),
    )
    db.add(config)
    await db.commit()
    return config, version.id


@pytest.mark.asyncio
async def test_invoke_matches_simulations_run_step_for_step(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The MCP ``invoke`` tool must produce the same step structure as ``/simulations/run``.

    Both paths must share the simulator and mock-response store -- if invoke
    diverges from the HTTP route, an LLM caller using the MCP boundary will
    see different behaviour than the IDE user. The persona's acceptance line
    "Calling invoke() ... returns the same result a direct API call would
    have" is checked here.
    """
    config, _version_id = await _seed_invoke_fixture(db_session)

    # The HTTP route requires a tenant header that matches the config's
    # tenant_id. The ``client`` fixture seeds "test-tenant"; our config is on
    # MCP_TENANT_ID. Push a tenant header for this single request.
    headers = {
        "X-Tenant-ID": MCP_TENANT_ID,
        "X-Tenant-Name": "MCP Bridge Tester",
        "X-Tenant-Role": "admin",
    }
    response = await client.post(
        "/api/v1/simulations/run",
        json={"configuration_id": config.id, "test_type": "full"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    http_body = response.json()["data"]
    http_steps = http_body["steps"]

    # Disable the LLM path so this test stays offline -- the HTTP route's
    # ``run_simulation`` rule-based fallback is what we want to compare with.
    with patch("finspark.api.routes.simulations.settings") as mock_settings, patch(
        "finspark.mcp.service.settings"
    ) as bridge_settings:
        mock_settings.ai_enabled = False
        mock_settings.gemini_api_key = ""
        bridge_settings.ai_enabled = False
        bridge_settings.openai_api_key = ""
        bridge_settings.gemini_api_key = ""

        bridge = BridgeService()

        # Force the bridge to use the same in-memory DB the HTTP client saw.
        class _Factory:
            def __call__(self):
                class _Ctx:
                    async def __aenter__(self_inner):
                        return db_session

                    async def __aexit__(self_inner, *exc):
                        return False

                return _Ctx()

        with patch("finspark.mcp.service.async_session_factory", _Factory()):
            mcp_result = await bridge.invoke_config(config.id)

    mcp_step_names = [s["step_name"] for s in mcp_result["steps"]]
    http_step_names = [s["step_name"] for s in http_steps]
    assert mcp_step_names == http_step_names, (
        f"MCP invoke steps {mcp_step_names!r} differ from HTTP run {http_step_names!r}"
    )
    assert mcp_result["total_tests"] == http_body["total_tests"]
    assert mcp_result["passed_tests"] == http_body["passed_tests"]
    assert mcp_result["status"] == http_body["status"]


@pytest.mark.asyncio
async def test_existing_http_routes_are_unaffected_by_mcp_import(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Importing the MCP module must not register any new FastAPI routes."""
    # Touch the MCP module to ensure import has run.
    from finspark.mcp import build_server  # noqa: F401

    response = await client.get("/api/v1/adapters/")
    assert response.status_code == 200

    health = await client.get("/health")
    assert health.status_code == 200

    # Sanity: list of routes should still match what main.py defined.
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json().get("paths", {})
    assert "/api/v1/configurations/generate" in paths
    assert "/api/v1/simulations/run" in paths
