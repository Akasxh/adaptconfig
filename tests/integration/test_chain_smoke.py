"""Integration test for the chain runtime through the simulator (MVP slice of #109).

Asserts:
  * A 2-step OAuth-then-resource config (smoke test_type) runs through
    :class:`ChainExecutor` rather than the per-endpoint loop, and the
    resource step's prepared request carries the token from step 1.
  * A cyclic config raises :class:`ChainCycleError` out of
    :meth:`IntegrationSimulator.run_simulation` so the route can turn it
    into HTTP 400.
  * Single-endpoint configs are unaffected (gold-standard fixture path).
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.adapter import Adapter, AdapterVersion
from finspark.models.configuration import Configuration
from finspark.services.chain import ChainCycleError, ChainExecutor
from finspark.services.simulation.simulator import IntegrationSimulator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _oauth_then_resource_config() -> dict:
    """A minimal 2-step chained config: OAuth token endpoint -> resource."""
    return {
        "adapter_name": "Payment Gateway",
        "version": "v1",
        "base_url": "https://payments.example.test",
        "auth": {"type": "oauth2"},
        "field_mappings": [
            {"source_field": "amount", "target_field": "amount", "confidence": 1.0},
        ],
        "endpoints": [
            {
                "id": "auth",
                "method": "POST",
                "path": "/payments/auth/token",
                "enabled": True,
                "headers": {},
                "body": {},
                "extract": {"token": "$.access_token"},
            },
            {
                "id": "resource",
                "method": "POST",
                "path": "/payments/create",
                "enabled": True,
                "depends_on": "auth",
                "headers": {},
                "body": {"amount": 1000},
                "inject": {
                    "headers.Authorization": "Bearer {{auth.token}}",
                },
            },
        ],
    }


def _single_endpoint_config() -> dict:
    """Single-endpoint config -- must NOT trigger chain mode."""
    return {
        "adapter_name": "Aadhaar eKYC Provider",
        "version": "v1",
        "base_url": "https://ekyc.example.test",
        "auth": {"type": "api_key"},
        "field_mappings": [
            {"source_field": "aadhaar_number", "target_field": "aadhaar_number",
             "confidence": 1.0},
        ],
        "endpoints": [
            {
                "method": "POST",
                "path": "/verify/aadhaar",
                "enabled": True,
            },
        ],
    }


def _cyclic_chained_config() -> dict:
    """Two endpoints depending on each other -- a cycle."""
    return {
        "adapter_name": "Payment Gateway",
        "version": "v1",
        "base_url": "https://payments.example.test",
        "auth": {"type": "oauth2"},
        "field_mappings": [],
        "endpoints": [
            {
                "id": "A",
                "method": "POST",
                "path": "/a",
                "enabled": True,
                "depends_on": "B",
            },
            {
                "id": "B",
                "method": "POST",
                "path": "/b",
                "enabled": True,
                "depends_on": "A",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Direct ChainExecutor integration -- isolates the chain-runtime contract
# ---------------------------------------------------------------------------


class TestChainExecutorIntegration:
    @pytest.mark.asyncio
    async def test_token_flows_from_auth_step_into_resource_step(self) -> None:
        """The whole point of #109 MVP: step B sees step A's extracted value."""
        endpoints = _oauth_then_resource_config()["endpoints"]
        seen_authz: dict[str, str] = {}

        async def fake_call(endpoint: dict, prepared_request: dict) -> dict:
            if endpoint["id"] == "auth":
                return {"access_token": "TOK_LIVE_42", "status": "success"}
            seen_authz["value"] = prepared_request.get("headers", {}).get(
                "Authorization", ""
            )
            return {
                "status": "success",
                "payment_id": "pay_001",
                "received_auth": seen_authz["value"],
            }

        executor = ChainExecutor(fake_call)
        results = await executor.run(endpoints)

        assert len(results) == 2
        assert results[0]["endpoint_id"] == "auth"
        assert results[0]["extracted"] == {"token": "TOK_LIVE_42"}
        assert results[1]["endpoint_id"] == "resource"
        assert results[1]["request"]["headers"]["Authorization"] == "Bearer TOK_LIVE_42"
        assert seen_authz["value"] == "Bearer TOK_LIVE_42"

    @pytest.mark.asyncio
    async def test_three_step_chain_orders_strictly(self) -> None:
        """A -> B -> C must execute in that order even when listed differently."""
        endpoints = [
            {"id": "C", "method": "GET", "path": "/c", "depends_on": "B"},
            {"id": "A", "method": "POST", "path": "/a"},
            {"id": "B", "method": "POST", "path": "/b", "depends_on": "A"},
        ]
        order: list[str] = []

        async def fake(endpoint: dict, request: dict) -> dict:
            order.append(endpoint["id"])
            return {"status": "success"}

        executor = ChainExecutor(fake)
        await executor.run(endpoints)
        assert order == ["A", "B", "C"]

    @pytest.mark.asyncio
    async def test_cycle_raises_chain_cycle_error(self) -> None:
        endpoints = _cyclic_chained_config()["endpoints"]

        async def never_called(endpoint: dict, request: dict) -> dict:
            raise AssertionError("call_fn should not be invoked when graph cycles")

        executor = ChainExecutor(never_called)
        with pytest.raises(ChainCycleError):
            await executor.run(endpoints)


# ---------------------------------------------------------------------------
# Through the simulator -- exercises the smoke-mode dispatch decision
# ---------------------------------------------------------------------------


class TestSimulatorChainSmoke:
    def test_smoke_with_chained_endpoints_dispatches_chain_executor(self) -> None:
        """test_type=smoke + 2 endpoints with depends_on must route through chain."""
        simulator = IntegrationSimulator()
        config = _oauth_then_resource_config()

        results = simulator.run_simulation(config, test_type="smoke")

        # Chain-driven steps are named "chained_endpoint_<id>"
        chain_step_names = [r.step_name for r in results if r.step_name.startswith("chained_endpoint_")]
        assert "chained_endpoint_auth" in chain_step_names
        assert "chained_endpoint_resource" in chain_step_names

        # The resource step must show the token threaded through inject
        resource_step = next(
            r for r in results if r.step_name == "chained_endpoint_resource"
        )
        injected = resource_step.actual_response.get("chain_context", {}).get(
            "injected_into_request", {}
        )
        assert injected.get("headers", {}).get("Authorization", "").startswith("Bearer ")
        # The token comes from the Payment mock's order_id / payment_id seeding,
        # which is deterministic but non-trivial -- we only assert the format.
        assert injected["headers"]["Authorization"] != "Bearer "

    def test_smoke_with_no_chained_endpoints_uses_per_endpoint_loop(self) -> None:
        """Single-endpoint config in smoke mode must NOT trigger chain dispatch.

        Guards the gold-standard 7/7 fixture path: it's a single-endpoint
        smoke run and should keep emitting endpoint_test_* step names.
        """
        simulator = IntegrationSimulator()
        config = _single_endpoint_config()

        results = simulator.run_simulation(config, test_type="smoke")

        # No chain step names produced
        chain_step_names = [r.step_name for r in results if r.step_name.startswith("chained_endpoint_")]
        assert chain_step_names == []
        # Per-endpoint test step is present instead
        endpoint_step_names = [r.step_name for r in results if r.step_name.startswith("endpoint_test_")]
        assert any("/verify/aadhaar" in n for n in endpoint_step_names)

    def test_full_with_chained_endpoints_skips_chain_executor(self) -> None:
        """Only test_type=smoke routes through chain -- per spec.

        Full-test mode runs the per-endpoint validation loop so the surrounding
        retry / error-handling steps still grade the config fairly.
        """
        simulator = IntegrationSimulator()
        config = _oauth_then_resource_config()

        results = simulator.run_simulation(config, test_type="full")

        chain_step_names = [r.step_name for r in results if r.step_name.startswith("chained_endpoint_")]
        assert chain_step_names == []

    def test_smoke_with_single_chained_endpoint_no_chain_dispatch(self) -> None:
        """Exactly one chained endpoint (the threshold is >=2) keeps per-endpoint mode."""
        simulator = IntegrationSimulator()
        config = _oauth_then_resource_config()
        config["endpoints"] = config["endpoints"][:1]  # just the auth step

        results = simulator.run_simulation(config, test_type="smoke")
        chain_step_names = [r.step_name for r in results if r.step_name.startswith("chained_endpoint_")]
        assert chain_step_names == []

    def test_smoke_cyclic_config_raises_chain_cycle_error(self) -> None:
        """Cycles propagate -- the route then translates to HTTP 400."""
        simulator = IntegrationSimulator()
        config = _cyclic_chained_config()

        with pytest.raises(ChainCycleError):
            simulator.run_simulation(config, test_type="smoke")


# ---------------------------------------------------------------------------
# Route-level: POST /api/v1/simulations/run must return 400 on cycles
# ---------------------------------------------------------------------------


async def _seed_chain_config(db: AsyncSession, *, cyclic: bool) -> Configuration:
    """Seed a Payment Gateway adapter + a Configuration whose full_config
    has a 2-step chain (or a cyclic 2-step chain).  Used by the route
    test to drive the HTTP path end-to-end."""
    adapter = Adapter(
        name="Payment Gateway",
        category="payment",
        description="chain runtime test adapter",
        is_active=True,
        icon="wallet",
    )
    db.add(adapter)
    await db.flush()
    version = AdapterVersion(
        adapter_id=adapter.id,
        version="v1",
        version_order=1,
        status="active",
        base_url="https://payments.example.test",
        auth_type="oauth2",
        endpoints=json.dumps(
            [
                {"path": "/payments/auth/token", "method": "POST", "description": "OAuth"},
                {"path": "/payments/create", "method": "POST", "description": "Charge"},
            ]
        ),
    )
    db.add(version)
    await db.flush()

    full_config = _cyclic_chained_config() if cyclic else _oauth_then_resource_config()
    config = Configuration(
        tenant_id="test-tenant",
        name="chain runtime smoke",
        adapter_version_id=version.id,
        status="configured",
        version=1,
        field_mappings=json.dumps(full_config.get("field_mappings", [])),
        transformation_rules=json.dumps([]),
        hooks=json.dumps([]),
        full_config=json.dumps(full_config),
    )
    db.add(config)
    await db.flush()
    return config


class TestRouteChainCycle:
    """End-to-end through the HTTP layer.

    These assert the cycle translation contract: ``ChainCycleError`` from
    the simulator must surface as HTTP 400 on the public API, not as a
    500 (which would leak an exception trace) or a 200 with a sneaky
    error step (which the UI couldn't act on cleanly).
    """

    @pytest.mark.asyncio
    async def test_cyclic_chain_returns_400(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        # Force the rule-based path by disabling AI for this call.  The
        # LLM-based validator runs against config shape, not execution,
        # so cycles only surface via the simulator -- and that only
        # happens on the rule-based path.
        from finspark.core.config import settings
        original = settings.ai_enabled
        settings.ai_enabled = False
        try:
            config = await _seed_chain_config(db_session, cyclic=True)
            response = await client.post(
                "/api/v1/simulations/run",
                json={"configuration_id": config.id, "test_type": "smoke"},
            )
        finally:
            settings.ai_enabled = original

        assert response.status_code == 400, response.text
        detail = response.json()["detail"].lower()
        assert "cycle" in detail

    @pytest.mark.asyncio
    async def test_acyclic_chain_returns_200(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from finspark.core.config import settings
        original = settings.ai_enabled
        settings.ai_enabled = False
        try:
            config = await _seed_chain_config(db_session, cyclic=False)
            response = await client.post(
                "/api/v1/simulations/run",
                json={"configuration_id": config.id, "test_type": "smoke"},
            )
        finally:
            settings.ai_enabled = original

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["data"]["configuration_id"] == config.id
        # At minimum, the two chained-endpoint steps must show up in the
        # results -- proving the chain executor ran, not the per-endpoint loop.
        step_names = {s["step_name"] for s in body["data"]["steps"]}
        assert "chained_endpoint_auth" in step_names
        assert "chained_endpoint_resource" in step_names
