"""Unit tests verifying the SSE stream endpoint uses fresh DB sessions in generators."""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from finspark.models.configuration import Configuration
from finspark.models.simulation import Simulation, SimulationStep


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


def _make_fresh_session_factory(sim_row=None):
    """Return a mock async_session_factory context manager for generator-internal use."""
    mock_session = AsyncMock()

    async def mock_execute(stmt):
        result = MagicMock()
        result.scalar_one.return_value = sim_row
        result.scalars.return_value.all.return_value = []
        return result

    mock_session.execute.side_effect = mock_execute
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    @asynccontextmanager
    async def factory():
        yield mock_session

    return factory, mock_session


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
        """SSE generator yields step events followed by done event.

        The run_and_stream generator uses async_session_factory internally
        to persist results, so we patch it.
        """
        from finspark.api.routes.simulations import stream_simulation
        from finspark.schemas.common import TenantContext
        from finspark.schemas.simulations import SimulationStepResult

        simulation = MagicMock(spec=Simulation)
        simulation.id = "sim-abc"
        simulation.configuration_id = "cfg-xyz"
        simulation.tenant_id = "test-tenant"
        simulation.status = "pending"
        simulation.test_type = "full"

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

        async def fake_stream(config, test_type=None):
            yield step1

        simulator.run_simulation_stream_async = fake_stream

        # Mock the fresh session used inside the generator
        factory, mock_session = _make_fresh_session_factory(sim_row=simulation)

        with patch("finspark.api.routes.simulations.async_session_factory", factory):
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
        assert "event: done" in full_body
        assert '"total_steps": 1' in full_body
        # Verify the fresh session committed results
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sse_event_generator_emits_error_event_on_exception(
        self, sample_full_config: dict
    ):
        """SSE generator emits error event and persists error status via fresh session."""
        from finspark.api.routes.simulations import stream_simulation
        from finspark.schemas.common import TenantContext

        simulation = MagicMock(spec=Simulation)
        simulation.id = "sim-err"
        simulation.configuration_id = "cfg-err"
        simulation.tenant_id = "test-tenant"
        simulation.status = "pending"
        simulation.test_type = "full"

        config = MagicMock(spec=Configuration)
        config.id = "cfg-err"
        config.full_config = json.dumps(sample_full_config)

        db = _make_db(simulation_row=simulation, config_row=config)
        tenant = TenantContext(tenant_id="test-tenant", tenant_name="Test", tenant_role="admin")

        simulator = MagicMock()

        async def failing_stream(config, test_type=None):
            raise RuntimeError("stream exploded")
            yield  # make it an async generator

        simulator.run_simulation_stream_async = failing_stream

        # The error path uses async_session_factory to persist error status
        err_sim = MagicMock(spec=Simulation)
        err_sim.id = "sim-err"
        err_sim.status = "running"
        factory, mock_session = _make_fresh_session_factory(sim_row=err_sim)

        with patch("finspark.api.routes.simulations.async_session_factory", factory):
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
        # Verify error status was persisted
        assert err_sim.status == "error"
        assert err_sim.error_log == "stream exploded"
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_replay_uses_fresh_session_for_completed_simulation(self):
        """Replay path opens a fresh DB session instead of using the DI-scoped one."""
        from finspark.api.routes.simulations import stream_simulation
        from finspark.schemas.common import TenantContext

        simulation = MagicMock(spec=Simulation)
        simulation.id = "sim-done"
        simulation.configuration_id = "cfg-done"
        simulation.tenant_id = "test-tenant"
        simulation.status = "passed"

        db = _make_db(simulation_row=simulation, config_row=None)
        tenant = TenantContext(tenant_id="test-tenant", tenant_name="Test", tenant_role="admin")
        simulator = MagicMock()

        # Build a mock stored step
        stored_step = MagicMock(spec=SimulationStep)
        stored_step.step_name = "schema_validation"
        stored_step.status = "passed"
        stored_step.request_payload = '{"pan": "ABCDE1234F"}'
        stored_step.expected_response = '{"score": 750}'
        stored_step.actual_response = '{"score": 750}'
        stored_step.duration_ms = 12
        stored_step.confidence_score = 0.95
        stored_step.error_message = None

        mock_session = AsyncMock()

        async def mock_execute(stmt):
            result = MagicMock()
            result.scalars.return_value.all.return_value = [stored_step]
            return result

        mock_session.execute.side_effect = mock_execute

        @asynccontextmanager
        async def factory():
            yield mock_session

        with patch("finspark.api.routes.simulations.async_session_factory", factory):
            response = await stream_simulation(
                simulation_id="sim-done",
                db=db,
                tenant=tenant,
                simulator=simulator,
            )

            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)

        full_body = "".join(chunks)
        assert "event: step" in full_body
        assert "schema_validation" in full_body
        assert "event: done" in full_body
        assert '"total_steps": 1' in full_body
        # Verify the fresh session was used (not the DI db)
        mock_session.execute.assert_awaited_once()
