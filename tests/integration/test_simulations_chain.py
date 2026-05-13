"""Integration tests for the chain-runtime path of `/api/v1/simulations/run`.

The persona's acceptance is two flows:

1. A 2-step OAuth-then-resource chain runs end-to-end and the protected
   step sees the access token extracted from the first step.
2. A cycle in ``depends_on`` returns HTTP 400 with a clear message.

Both tests use the existing ASGI ``client`` fixture (from tests/conftest.py)
so we don't have to stand up a real uvicorn -- parallel agents can run
simultaneously without fighting for port 8000.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.adapter import Adapter, AdapterVersion
from finspark.models.configuration import Configuration


async def _seed_adapter_version(db: AsyncSession) -> AdapterVersion:
    adapter = Adapter(
        name="ChainTest Adapter",
        category="custom",
        description="Adapter used for chain-runtime integration tests",
        is_active=True,
        icon="link",
    )
    db.add(adapter)
    await db.flush()

    version = AdapterVersion(
        adapter_id=adapter.id,
        version="v1",
        version_order=1,
        status="active",
        base_url="https://api.example.com/v1",
        auth_type="oauth2",
        endpoints=json.dumps(
            [
                {"path": "/oauth/token", "method": "POST", "description": "Obtain token"},
                {"path": "/protected", "method": "POST", "description": "Protected resource"},
            ]
        ),
    )
    db.add(version)
    await db.flush()
    return version


def _chain_config(version_id: str) -> Configuration:
    full_config: dict[str, Any] = {
        "adapter_name": "ChainTest Adapter",
        "version": "v1",
        "base_url": "https://api.example.com/v1",
        "auth": {"type": "oauth2"},
        "endpoints": [
            {
                "id": "auth",
                "path": "/oauth/token",
                "method": "POST",
                "description": "Obtain access token",
                "extract": [
                    {"name": "access_token", "path": "$.access_token"},
                ],
            },
            {
                "id": "resource",
                "path": "/protected",
                "method": "POST",
                "description": "Call protected resource",
                "depends_on": ["auth"],
                "inject": [
                    {"from": "access_token", "to": "access_token"},
                ],
            },
        ],
        "field_mappings": [
            {"source_field": "pan_number", "target_field": "pan", "confidence": 0.9}
        ],
        "transformation_rules": [],
        "hooks": [],
        "retry_policy": {"max_retries": 3, "backoff_factor": 2, "retry_on_status": [503]},
    }
    return Configuration(
        tenant_id="test-tenant",
        name="Chain Test Config",
        adapter_version_id=version_id,
        status="configured",
        version=1,
        field_mappings=json.dumps(full_config["field_mappings"]),
        transformation_rules=json.dumps([]),
        hooks=json.dumps([]),
        full_config=json.dumps(full_config),
    )


def _cycle_config(version_id: str) -> Configuration:
    full_config: dict[str, Any] = {
        "adapter_name": "ChainTest Adapter",
        "version": "v1",
        "base_url": "https://api.example.com/v1",
        "auth": {"type": "oauth2"},
        "endpoints": [
            {"id": "a", "path": "/a", "method": "POST", "depends_on": ["b"]},
            {"id": "b", "path": "/b", "method": "POST", "depends_on": ["a"]},
        ],
        "field_mappings": [],
        "transformation_rules": [],
        "hooks": [],
    }
    return Configuration(
        tenant_id="test-tenant",
        name="Cycle Test Config",
        adapter_version_id=version_id,
        status="configured",
        version=1,
        field_mappings=json.dumps([]),
        transformation_rules=json.dumps([]),
        hooks=json.dumps([]),
        full_config=json.dumps(full_config),
    )


# ---------------------------------------------------------------------------
# Happy path: two-step OAuth-then-resource chain
# ---------------------------------------------------------------------------


class TestSimulationChainHappyPath:
    @pytest.mark.asyncio
    async def test_two_step_chain_runs_and_injects_token(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Token endpoint runs first, protected endpoint sees ``access_token``."""
        # Patch the mock server so we can both:
        #   (a) deterministically return an access_token from /oauth/token,
        #   (b) capture what the protected endpoint receives.
        captured: dict[str, dict[str, Any]] = {}

        def fake_generate_response(
            self: Any,
            endpoint: dict[str, Any],
            request_payload: dict[str, Any],
            response_schema: dict[str, Any] | None = None,
            config: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            path = endpoint.get("path", "")
            captured[path] = dict(request_payload)
            if path == "/oauth/token":
                return {
                    "status": "success",
                    "access_token": "chain-token-xyz",
                    "expires_in": 3600,
                }
            if path == "/protected":
                return {
                    "status": "success",
                    "saw_token": request_payload.get("access_token"),
                }
            return {"status": "success"}

        from finspark.services.simulation.simulator import MockAPIServer

        monkeypatch.setattr(
            MockAPIServer, "generate_response", fake_generate_response, raising=True
        )

        version = await _seed_adapter_version(db_session)
        config = _chain_config(version.id)
        db_session.add(config)
        await db_session.flush()

        response = await client.post(
            "/api/v1/simulations/run",
            json={"configuration_id": config.id, "test_type": "smoke"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        steps = body["data"]["steps"]
        chain_step_names = [s["step_name"] for s in steps if s["step_name"].startswith("chain_step_")]
        assert len(chain_step_names) == 2

        # The protected step's request payload must contain the token from
        # the auth step's response.
        assert captured["/oauth/token"] != captured.get("/protected")
        assert captured["/protected"]["access_token"] == "chain-token-xyz"

        # And the simulation step records the same.
        protected_step = next(
            s for s in steps if s["step_name"].endswith("_/protected")
        )
        assert protected_step["actual_response"]["saw_token"] == "chain-token-xyz"


# ---------------------------------------------------------------------------
# Cycle detection: must return 400
# ---------------------------------------------------------------------------


class TestSimulationChainCycle:
    @pytest.mark.asyncio
    async def test_cycle_in_depends_on_returns_400(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        version = await _seed_adapter_version(db_session)
        config = _cycle_config(version.id)
        db_session.add(config)
        await db_session.flush()

        response = await client.post(
            "/api/v1/simulations/run",
            json={"configuration_id": config.id, "test_type": "smoke"},
        )
        assert response.status_code == 400, response.text
        detail = response.json()["detail"]
        assert "chain" in detail.lower()
        assert "cycle" in detail.lower()


# ---------------------------------------------------------------------------
# Single-endpoint configs are unaffected by the chain runtime.
# ---------------------------------------------------------------------------


class TestSimulationSingleEndpointUnaffected:
    @pytest.mark.asyncio
    async def test_single_endpoint_config_does_not_trigger_chain_steps(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        version = await _seed_adapter_version(db_session)
        full_config = {
            "adapter_name": "ChainTest Adapter",
            "version": "v1",
            "base_url": "https://api.example.com/v1",
            "auth": {"type": "api_key"},
            "endpoints": [
                {"id": "only", "path": "/only", "method": "POST"},
            ],
            "field_mappings": [
                {"source_field": "pan_number", "target_field": "pan", "confidence": 0.9}
            ],
            "transformation_rules": [],
            "hooks": [],
        }
        config = Configuration(
            tenant_id="test-tenant",
            name="Single Endpoint Config",
            adapter_version_id=version.id,
            status="configured",
            version=1,
            field_mappings=json.dumps(full_config["field_mappings"]),
            transformation_rules=json.dumps([]),
            hooks=json.dumps([]),
            full_config=json.dumps(full_config),
        )
        db_session.add(config)
        await db_session.flush()

        response = await client.post(
            "/api/v1/simulations/run",
            json={"configuration_id": config.id, "test_type": "smoke"},
        )
        assert response.status_code == 200
        steps = response.json()["data"]["steps"]
        assert all(not s["step_name"].startswith("chain_step_") for s in steps)
