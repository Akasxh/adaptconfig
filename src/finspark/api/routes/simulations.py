"""Simulation and testing routes."""

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.api.dependencies import get_audit_service, get_simulator, get_tenant_context
from finspark.core.audit import AuditService
from finspark.core.database import get_db
from finspark.models.configuration import Configuration
from finspark.models.simulation import Simulation, SimulationStep
from finspark.schemas.common import APIResponse, TenantContext
from finspark.schemas.simulations import (
    RunSimulationRequest,
    SimulationResponse,
    SimulationStepResult,
)
from finspark.services.simulation.simulator import IntegrationSimulator

router = APIRouter(prefix="/simulations", tags=["Simulations"])


@router.post("/run", response_model=APIResponse[SimulationResponse])
async def run_simulation(
    request: RunSimulationRequest,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
    simulator: IntegrationSimulator = Depends(get_simulator),
    audit: AuditService = Depends(get_audit_service),
) -> APIResponse[SimulationResponse]:
    """Run a simulation/test against a configuration."""
    # Fetch configuration
    stmt = select(Configuration).where(
        Configuration.id == request.configuration_id,
        Configuration.tenant_id == tenant.tenant_id,
    )
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()
    if not config or not config.full_config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    full_config = json.loads(config.full_config)

    # Create simulation record
    simulation = Simulation(
        tenant_id=tenant.tenant_id,
        configuration_id=request.configuration_id,
        status="running",
        test_type=request.test_type,
    )
    db.add(simulation)
    await db.flush()

    # Run simulation
    steps = simulator.run_simulation(full_config, test_type=request.test_type)

    # Save results
    total = len(steps)
    passed = sum(1 for s in steps if s.status == "passed")
    failed = total - passed
    total_duration = sum(s.duration_ms for s in steps)

    simulation.status = "passed" if failed == 0 else "failed"
    simulation.total_tests = total
    simulation.passed_tests = passed
    simulation.failed_tests = failed
    simulation.duration_ms = total_duration
    simulation.results = json.dumps([s.model_dump() for s in steps])

    # Save individual steps
    for i, step in enumerate(steps):
        sim_step = SimulationStep(
            simulation_id=simulation.id,
            step_name=step.step_name,
            step_order=i,
            status=step.status,
            request_payload=json.dumps(step.request_payload),
            expected_response=json.dumps(step.expected_response),
            actual_response=json.dumps(step.actual_response),
            duration_ms=step.duration_ms,
            confidence_score=step.confidence_score,
            error_message=step.error_message,
        )
        db.add(sim_step)

    # Update config status
    config.status = "testing" if simulation.status == "passed" else "configured"

    await audit.log(
        tenant_id=tenant.tenant_id,
        actor=tenant.tenant_name,
        action="run_simulation",
        resource_type="simulation",
        resource_id=simulation.id,
        details={
            "config_id": request.configuration_id,
            "status": simulation.status,
            "passed": passed,
            "failed": failed,
        },
    )

    return APIResponse(
        data=SimulationResponse(
            id=simulation.id,
            configuration_id=simulation.configuration_id,
            status=simulation.status,
            test_type=simulation.test_type,
            total_tests=total,
            passed_tests=passed,
            failed_tests=failed,
            duration_ms=total_duration,
            steps=steps,
            created_at=simulation.created_at,
        ),
        message=f"Simulation {simulation.status}: {passed}/{total} tests passed",
    )


@router.get("/{simulation_id}", response_model=APIResponse[SimulationResponse])
async def get_simulation(
    simulation_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[SimulationResponse]:
    """Get simulation results."""
    stmt = select(Simulation).where(
        Simulation.id == simulation_id,
        Simulation.tenant_id == tenant.tenant_id,
    )
    result = await db.execute(stmt)
    simulation = result.scalar_one_or_none()
    if not simulation:
        raise HTTPException(status_code=404, detail="Simulation not found")

    steps = []
    if simulation.results:
        steps = [SimulationStepResult(**s) for s in json.loads(simulation.results)]

    return APIResponse(
        data=SimulationResponse(
            id=simulation.id,
            configuration_id=simulation.configuration_id,
            status=simulation.status,
            test_type=simulation.test_type,
            total_tests=simulation.total_tests,
            passed_tests=simulation.passed_tests,
            failed_tests=simulation.failed_tests,
            duration_ms=simulation.duration_ms,
            steps=steps,
            created_at=simulation.created_at,
        ),
    )


@router.get("/{simulation_id}/stream")
async def stream_simulation(
    simulation_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
    simulator: IntegrationSimulator = Depends(get_simulator),
) -> StreamingResponse:
    """Stream simulation step results as Server-Sent Events."""
    # Look up the Simulation record first
    sim_stmt = select(Simulation).where(
        Simulation.id == simulation_id,
        Simulation.tenant_id == tenant.tenant_id,
    )
    sim_result = await db.execute(sim_stmt)
    simulation = sim_result.scalar_one_or_none()
    if not simulation:
        raise HTTPException(status_code=404, detail="Simulation not found")

    # Fetch the Configuration via the simulation's configuration_id
    cfg_stmt = select(Configuration).where(
        Configuration.id == simulation.configuration_id,
        Configuration.tenant_id == tenant.tenant_id,
    )
    cfg_result = await db.execute(cfg_stmt)
    config = cfg_result.scalar_one_or_none()
    if not config or not config.full_config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    full_config = json.loads(config.full_config)

    async def event_generator() -> AsyncGenerator[str, None]:
        step_index = 0
        try:
            # run_simulation_stream is a sync generator; run it off the event loop thread
            steps = await asyncio.to_thread(
                lambda: list(simulator.run_simulation_stream(full_config))
            )
            for step in steps:
                data = json.dumps(step.model_dump())
                yield f"event: step\ndata: {data}\n\n"
                step_index += 1
            yield f'event: done\ndata: {{"total_steps": {step_index}}}\n\n'
        except Exception as exc:
            error_data = json.dumps({"message": str(exc), "steps_completed": step_index})
            yield f"event: error\ndata: {error_data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
