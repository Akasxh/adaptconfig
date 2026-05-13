"""Schemas for simulation and testing."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from finspark.schemas.common import SimulationStatus


class RunSimulationRequest(BaseModel):
    """Request to run a simulation."""

    configuration_id: str
    test_type: str = "full"  # full, smoke, schema_only, parallel_version
    mock_responses: dict[str, Any] | None = None


class SimulationStepResult(BaseModel):
    """Result of a single simulation step."""

    step_name: str
    status: str  # passed, failed, skipped, error
    request_payload: dict[str, Any] = {}
    expected_response: dict[str, Any] = {}
    actual_response: dict[str, Any] = {}
    duration_ms: int = 0
    confidence_score: float = 0.0
    error_message: str | None = None
    assertions: list[dict[str, Any]] = []


class SimulationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    configuration_id: str
    status: SimulationStatus
    test_type: str
    total_tests: int
    passed_tests: int
    failed_tests: int
    duration_ms: int | None = None
    steps: list[SimulationStepResult] = []
    chain_run: dict[str, Any] | None = None  # Populated when test_type=="chain": graph, edges, cascade analysis.
    created_at: datetime
