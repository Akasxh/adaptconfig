"""Unit tests for the MCP bridge (Issue #114).

Covers tool registration, the env-token auth gate, and the
:class:`BridgeService` wiring against the existing service classes -- all
without touching the network or spawning a subprocess.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from finspark.mcp import BridgeService, build_server
from finspark.mcp.__main__ import MCP_TOKEN_ENV, StartupError, check_auth
from finspark.mcp.server import SERVER_NAME, TOOL_NAMES
from finspark.mcp.service import MCP_TENANT_ID, BridgeError
from finspark.models.adapter import Adapter, AdapterVersion
from finspark.models.configuration import Configuration
from finspark.schemas.documents import (
    ExtractedEndpoint,
    ExtractedField,
    ParsedDocumentResult,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_adapter(db_session):
    """Seed a minimal CIBIL adapter so the bridge has something to score."""
    adapter = Adapter(
        name="CIBIL Credit Bureau",
        category="bureau",
        description="TransUnion CIBIL credit score integration",
        is_active=True,
        icon="credit-card",
    )
    db_session.add(adapter)
    await db_session.flush()

    version = AdapterVersion(
        adapter_id=adapter.id,
        version="v1",
        version_order=1,
        status="active",
        base_url="https://api.cibil.com/v1",
        auth_type="api_key",
        endpoints=json.dumps(
            [
                {"path": "/credit-score", "method": "POST", "description": "Fetch score"},
                {"path": "/credit-report", "method": "POST", "description": "Fetch report"},
            ]
        ),
        request_schema=json.dumps(
            {
                "type": "object",
                "properties": {
                    "pan_number": {"type": "string"},
                    "full_name": {"type": "string"},
                    "date_of_birth": {"type": "string"},
                },
            }
        ),
        response_schema=json.dumps(
            {"type": "object", "properties": {"credit_score": {"type": "integer"}}}
        ),
    )
    db_session.add(version)
    await db_session.commit()
    return adapter, version


@pytest_asyncio.fixture
async def patch_session_factory(db_session):
    """Pin ``async_session_factory`` in the bridge to the test in-memory DB.

    Yields nothing -- the patch is applied via context-manager scope.
    """

    class _Factory:
        def __call__(self):
            class _Ctx:
                async def __aenter__(self):
                    return db_session

                async def __aexit__(self, *exc):
                    return False

            return _Ctx()

    with patch("finspark.mcp.service.async_session_factory", _Factory()):
        yield


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_server_exposes_three_named_tools() -> None:
    server = build_server()
    assert server.name == SERVER_NAME

    listed = await server.list_tools()
    listed_names = {tool.name for tool in listed}
    assert listed_names == set(TOOL_NAMES)
    assert listed_names == {"list_adapters", "generate_config", "invoke"}


@pytest.mark.asyncio
async def test_each_tool_has_a_non_empty_description() -> None:
    server = build_server()
    tools = await server.list_tools()
    for tool in tools:
        assert tool.description, f"tool {tool.name} has no description"


def test_build_server_accepts_injected_bridge() -> None:
    stub = MagicMock(spec=BridgeService)
    server = build_server(stub)
    assert server.name == SERVER_NAME


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_check_auth_returns_token_when_present() -> None:
    token = check_auth({MCP_TOKEN_ENV: "secret-value"})
    assert token == "secret-value"


def test_check_auth_allows_missing_token_in_debug_mode() -> None:
    with patch("finspark.mcp.__main__.settings") as mock_settings:
        mock_settings.debug = True
        assert check_auth({}) is None


def test_check_auth_rejects_missing_token_in_non_debug() -> None:
    with patch("finspark.mcp.__main__.settings") as mock_settings:
        mock_settings.debug = False
        with pytest.raises(StartupError) as excinfo:
            check_auth({})
        assert MCP_TOKEN_ENV in str(excinfo.value)


def test_check_auth_treats_empty_string_as_missing() -> None:
    with patch("finspark.mcp.__main__.settings") as mock_settings:
        mock_settings.debug = False
        with pytest.raises(StartupError):
            check_auth({MCP_TOKEN_ENV: ""})


# ---------------------------------------------------------------------------
# BridgeService wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_adapters_summary_returns_seeded_catalogue(
    db_session, seeded_adapter, patch_session_factory
) -> None:
    adapter, version = seeded_adapter
    bridge = BridgeService()
    summary = await bridge.list_adapters_summary()

    assert summary["total"] == 1
    assert "bureau" in summary["categories"]
    assert len(summary["adapters"]) == 1

    first = summary["adapters"][0]
    assert first["name"] == adapter.name
    assert first["category"] == "bureau"
    assert len(first["versions"]) == 1

    version_summary = first["versions"][0]
    assert version_summary["id"] == version.id
    assert version_summary["auth_type"] == "api_key"
    assert any(ep["path"] == "/credit-score" for ep in version_summary["endpoints"])


@pytest.mark.asyncio
async def test_generate_config_persists_a_configuration_row(
    db_session, seeded_adapter, patch_session_factory
) -> None:
    _adapter, version = seeded_adapter

    # Disable the LLM path so this test stays offline.
    with patch("finspark.mcp.service.settings") as mock_settings:
        mock_settings.ai_enabled = False
        mock_settings.openai_api_key = ""
        mock_settings.gemini_api_key = ""

        bridge = BridgeService()
        document_text = (
            "Integrate CIBIL credit score API. The system must POST to "
            "/credit-score with pan_number, full_name and date_of_birth and "
            "return the credit_score field."
        )
        result = await bridge.generate_config_from_text(document_text)

    assert result["adapter_name"] == "CIBIL Credit Bureau"
    assert result["adapter_version"] == "v1"
    assert result["generation_path"] == "rule_based"
    assert result["config_id"]

    persisted = await db_session.execute(
        select(Configuration).where(Configuration.id == result["config_id"])
    )
    row = persisted.scalar_one()
    assert row.tenant_id == MCP_TENANT_ID
    assert row.adapter_version_id == version.id
    full = json.loads(row.full_config)
    assert full["adapter_name"] == "CIBIL Credit Bureau"
    assert full["_generation_path"] == "rule_based"


@pytest.mark.asyncio
async def test_generate_config_honours_adapter_hint(
    db_session, patch_session_factory
) -> None:
    # Seed two adapters so the hint actually has to discriminate.
    kyc = Adapter(name="DigiLocker KYC", category="kyc", is_active=True)
    bureau = Adapter(name="CIBIL Credit Bureau", category="bureau", is_active=True)
    db_session.add_all([kyc, bureau])
    await db_session.flush()
    for parent in (kyc, bureau):
        db_session.add(
            AdapterVersion(
                adapter_id=parent.id,
                version="v1",
                version_order=1,
                status="active",
                base_url=f"https://api.{parent.category}.test/v1",
                auth_type="api_key",
                endpoints=json.dumps(
                    [{"path": f"/{parent.category}", "method": "POST"}]
                ),
                request_schema=json.dumps({"properties": {"pan_number": {"type": "string"}}}),
                response_schema=json.dumps({"properties": {}}),
            )
        )
    await db_session.commit()

    with patch("finspark.mcp.service.settings") as mock_settings:
        mock_settings.ai_enabled = False
        mock_settings.openai_api_key = ""
        mock_settings.gemini_api_key = ""

        bridge = BridgeService()
        result = await bridge.generate_config_from_text(
            "We need a KYC integration that consumes a pan_number.",
            adapter_hint="KYC",
        )

    assert "kyc" in result["adapter_name"].lower()


@pytest.mark.asyncio
async def test_generate_config_rejects_empty_text(patch_session_factory) -> None:
    bridge = BridgeService()
    with pytest.raises(BridgeError):
        await bridge.generate_config_from_text("   ")


@pytest.mark.asyncio
async def test_generate_config_raises_when_catalogue_is_empty(
    patch_session_factory,
) -> None:
    bridge = BridgeService()
    with patch("finspark.mcp.service.settings") as mock_settings:
        mock_settings.ai_enabled = False
        mock_settings.openai_api_key = ""
        mock_settings.gemini_api_key = ""

        with pytest.raises(BridgeError) as excinfo:
            await bridge.generate_config_from_text("Some BRD with pan_number field")
    assert "adapter" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_invoke_runs_simulator_and_returns_steps(
    db_session, seeded_adapter, patch_session_factory
) -> None:
    _adapter, version = seeded_adapter

    full_config = {
        "adapter_name": "CIBIL Credit Bureau",
        "version": "v1",
        "base_url": "https://api.cibil.com/v1",
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

    config_row = Configuration(
        tenant_id=MCP_TENANT_ID,
        name="invoke-fixture",
        adapter_version_id=version.id,
        status="configured",
        version=1,
        field_mappings=json.dumps(full_config["field_mappings"]),
        transformation_rules=json.dumps([]),
        hooks=json.dumps(full_config["hooks"]),
        full_config=json.dumps(full_config),
    )
    db_session.add(config_row)
    await db_session.commit()

    bridge = BridgeService()
    result = await bridge.invoke_config(
        config_row.id, payload={"pan_number": "ABCDE1234F"}
    )

    assert result["config_id"] == config_row.id
    assert result["total_tests"] >= 5
    assert result["status"] in {"passed", "failed"}
    assert result["payload_applied"] == {"pan_number": "ABCDE1234F"}
    assert isinstance(result["final_response"], dict)
    assert any(
        step["step_name"].startswith("endpoint_test_") for step in result["steps"]
    )


@pytest.mark.asyncio
async def test_invoke_raises_for_unknown_config(patch_session_factory) -> None:
    bridge = BridgeService()
    with pytest.raises(BridgeError) as excinfo:
        await bridge.invoke_config("does-not-exist")
    assert "not found" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_inject_payload_does_not_mutate_original_config() -> None:
    original = {
        "field_mappings": [
            {"source_field": "pan_number", "target_field": "pan", "confidence": 0.9}
        ]
    }
    merged = BridgeService._inject_payload(original, {"pan_number": "OVERRIDE"})
    assert merged is not original
    assert original["field_mappings"][0].get("sample_value") is None or "sample_value" not in original["field_mappings"][0]
    assert any(
        m.get("sample_value") == "OVERRIDE" for m in merged["field_mappings"]
    )


@pytest.mark.asyncio
async def test_parse_document_text_uses_llm_when_enabled(monkeypatch) -> None:
    bridge = BridgeService()

    fake_parsed = ParsedDocumentResult(
        doc_type="brd",
        title="LLM Parsed",
        endpoints=[ExtractedEndpoint(path="/x", method="POST")],
        fields=[ExtractedField(name="pan_number", data_type="string", is_required=True)],
    )
    llm_mock = AsyncMock(return_value=fake_parsed)
    monkeypatch.setattr(BridgeService, "_parse_document_text", llm_mock)

    result = await bridge._parse_document_text("hello world")
    assert result.title == "LLM Parsed"
    llm_mock.assert_awaited_once_with("hello world")
