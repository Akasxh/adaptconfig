"""Unit tests verifying the SSE stream endpoint queries Simulation first."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from finspark.models.configuration import Configuration
from finspark.models.simulation import Simulation


@pytest.fixture
def sample_full_config() -> dict:
    return {
        "adapter_name": "CIBIL Credit Bureau",
        "version": "v1",
        "base_url": "https://api.cibil.com/v1",
        "auth": {"type": "api_key", "credentials": {}},
        "endpoints": [{"path": "/credit-score", "method": "POST", "enabled": True}],
        "field_mappings": [
            {"source_field": "pan_number", "target_field": "pan", "confidence": 1.0}
        ],
        "transformation_rules": [],
        "hooks": [],
        "retry_policy": {"max_retries": 3, "backoff_factor": 2, "retry_on_status": [429, 500]},
        "timeout_ms": 30000,
    }


def _make_db(simulation_row, config_row):
    """Return a mock AsyncSession whose execute() yields the given rows in order."""
    calls = []

    async def execute(stmt):
        calls.append(stmt)
        result = MagicMock()
        if len(calls) == 1:
            # First call: Simulation lookup
            result.scalar_one_or_none.return_value = simulation_row
        else:
            # Second call: Configuration lookup
            result.scalar_one_or_none.return_value = config_row
        return result

    db = AsyncMock()
    db.execute.side_effect = execute
    return db


class TestStreamEndpointLooksUpSimulationFirst:
    @pytest.mark.asyncio
    async def test_404_when_simulation_not_found(self):
        """Endpoint raises 404 if simulation_id doesn't match any Simulation row."""
        from finspark.api.routes.simulations import stream_simulation
        from finspark.schemas.common import TenantContext

        db = _make_db(simulation_row=None, config_row=None)
        tenant = TenantContext(tenant_id="test-tenant", tenant_name="Test", tenant_role="admin")
        simulator = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await stream_simulation(
                simulation_id="nonexistent-sim-id",
                db=db,
                tenant=tenant,
                simulator=simulator,
            )

        assert exc_info.value.status_code == 404
        assert "simulation" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_fetches_configuration_via_simulation_configuration_id(
        self, sample_full_config: dict
    ):
        """Endpoint uses simulation.configuration_id to look up Configuration."""
        from finspark.api.routes.simulations import stream_simulation
        from finspark.schemas.common import TenantContext
        from finspark.schemas.simulations import SimulationStepResult

        simulation = MagicMock(spec=Simulation)
        simulation.id = "sim-123"
        simulation.configuration_id = "config-456"
        simulation.tenant_id = "test-tenant"

        config = MagicMock(spec=Configuration)
        config.id = "config-456"
        config.full_config = json.dumps(sample_full_config)

        db = _make_db(simulation_row=simulation, config_row=config)
        tenant = TenantContext(tenant_id="test-tenant", tenant_name="Test", tenant_role="admin")

        fake_step = SimulationStepResult(
            step_name="config_structure_validation",
            status="passed",
            request_payload={},
            expected_response={},
            actual_response={},
            duration_ms=10,
            confidence_score=1.0,
        )
        simulator = MagicMock()
        simulator.run_simulation_stream.return_value = iter([fake_step])

        response = await stream_simulation(
            simulation_id="sim-123",
            db=db,
            tenant=tenant,
            simulator=simulator,
        )

        # Response should be a StreamingResponse
        from fastapi.responses import StreamingResponse

        assert isinstance(response, StreamingResponse)
        assert response.media_type == "text/event-stream"

    @pytest.mark.asyncio
    async def test_404_when_configuration_not_found_after_simulation(self):
        """Endpoint raises 404 if the linked Configuration is missing."""
        from finspark.api.routes.simulations import stream_simulation
        from finspark.schemas.common import TenantContext

        simulation = MagicMock(spec=Simulation)
        simulation.id = "sim-123"
        simulation.configuration_id = "config-missing"
        simulation.tenant_id = "test-tenant"

        db = _make_db(simulation_row=simulation, config_row=None)
        tenant = TenantContext(tenant_id="test-tenant", tenant_name="Test", tenant_role="admin")
        simulator = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await stream_simulation(
                simulation_id="sim-123",
                db=db,
                tenant=tenant,
                simulator=simulator,
            )

        assert exc_info.value.status_code == 404
        assert "configuration" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_sse_event_generator_emits_step_and_done_events(
        self, sample_full_config: dict
    ):
        """SSE generator yields step events followed by done event."""
        from finspark.api.routes.simulations import stream_simulation
        from finspark.schemas.common import TenantContext
        from finspark.schemas.simulations import SimulationStepResult

        simulation = MagicMock(spec=Simulation)
        simulation.id = "sim-abc"
        simulation.configuration_id = "cfg-xyz"
        simulation.tenant_id = "test-tenant"

        config = MagicMock(spec=Configuration)
        config.id = "cfg-xyz"
        config.full_config = json.dumps(sample_full_config)

        db = _make_db(simulation_row=simulation, config_row=config)
        tenant = TenantContext(tenant_id="test-tenant", tenant_name="Test", tenant_role="admin")

        step1 = SimulationStepResult(
            step_name="auth_config_validation",
            status="passed",
            request_payload={},
            expected_response={},
            actual_response={},
            duration_ms=5,
            confidence_score=0.9,
        )
        simulator = MagicMock()
        simulator.run_simulation_stream.return_value = iter([step1])

        response = await stream_simulation(
            simulation_id="sim-abc",
            db=db,
            tenant=tenant,
            simulator=simulator,
        )

        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)

        full_body = "".join(chunks)
        assert "event: step" in full_body
        assert "auth_config_validation" in full_body
        assert 'event: done' in full_body
        assert '"total_steps": 1' in full_body

    @pytest.mark.asyncio
    async def test_sse_event_generator_emits_error_event_on_exception(
        self, sample_full_config: dict
    ):
        """SSE generator emits error event when streaming raises an exception."""
        from finspark.api.routes.simulations import stream_simulation
        from finspark.schemas.common import TenantContext

        simulation = MagicMock(spec=Simulation)
        simulation.id = "sim-err"
        simulation.configuration_id = "cfg-err"
        simulation.tenant_id = "test-tenant"

        config = MagicMock(spec=Configuration)
        config.id = "cfg-err"
        config.full_config = json.dumps(sample_full_config)

        db = _make_db(simulation_row=simulation, config_row=config)
        tenant = TenantContext(tenant_id="test-tenant", tenant_name="Test", tenant_role="admin")

        simulator = MagicMock()
        simulator.run_simulation_stream.side_effect = RuntimeError("stream exploded")

        response = await stream_simulation(
            simulation_id="sim-err",
            db=db,
            tenant=tenant,
            simulator=simulator,
        )

        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)

        full_body = "".join(chunks)
        assert "event: error" in full_body
        assert "stream exploded" in full_body
