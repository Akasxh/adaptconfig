"""Simulation and testing routes."""

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.api.dependencies import get_audit_service, get_simulator, get_tenant_context, require_role
from finspark.core.json_utils import safe_json_loads
from finspark.core.audit import AuditService
from finspark.core.database import async_session_factory, get_db
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


@router.get("/", response_model=APIResponse[list[SimulationResponse]])
async def list_simulations(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[list[SimulationResponse]]:
    """List simulations for the current tenant (most recent first, limit 50)."""
    stmt = (
        select(Simulation)
        .where(Simulation.tenant_id == tenant.tenant_id)
        .order_by(Simulation.created_at.desc())
        .limit(50)
    )
    result = await db.execute(stmt)
    sims = result.scalars().all()

    data = []
    for sim in sims:
        steps = []
        if sim.results:
            parsed = safe_json_loads(sim.results, [])
            steps = [SimulationStepResult(**s) for s in parsed]
        data.append(
            SimulationResponse(
                id=sim.id,
                configuration_id=sim.configuration_id,
                status=sim.status,
                test_type=sim.test_type,
                total_tests=sim.total_tests,
                passed_tests=sim.passed_tests,
                failed_tests=sim.failed_tests,
                duration_ms=sim.duration_ms,
                steps=steps,
                created_at=sim.created_at,
            )
        )
    return APIResponse(success=True, data=data, message="")


@router.post("/run", response_model=APIResponse[SimulationResponse])
async def run_simulation(
    request: RunSimulationRequest,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = require_role("admin", "editor"),
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

    full_config = safe_json_loads(config.full_config, {})

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
    steps = await asyncio.to_thread(simulator.run_simulation, full_config, test_type=request.test_type)

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
        steps = [SimulationStepResult(**s) for s in safe_json_loads(simulation.results, [])]

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


def _serialize_step(step: SimulationStep) -> dict:
    """Serialize a SimulationStep ORM row to a dict matching SimulationStepResult."""
    return {
        "step_name": step.step_name,
        "status": step.status,
        "request_payload": json.loads(step.request_payload or "{}"),
        "expected_response": json.loads(step.expected_response or "{}"),
        "actual_response": json.loads(step.actual_response or "{}"),
        "duration_ms": step.duration_ms or 0,
        "confidence_score": step.confidence_score or 0.0,
        "error_message": step.error_message,
        "assertions": [],
    }


@router.get("/{simulation_id}/stream")
async def stream_simulation(
    simulation_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
    simulator: IntegrationSimulator = Depends(get_simulator),
) -> StreamingResponse:
    """Stream simulation step results as Server-Sent Events.

    Replays stored steps from DB if the simulation is already complete.
    Otherwise runs the simulation fresh with per-step timeout, then persists results.

    Uses a fresh DB session inside generators to avoid operating on the
    DI-scoped session after the request handler returns (GitHub issue #70).
    """
    sim_stmt = select(Simulation).where(
        Simulation.id == simulation_id,
        Simulation.tenant_id == tenant.tenant_id,
    )
    sim_result = await db.execute(sim_stmt)
    simulation = sim_result.scalar_one_or_none()
    if not simulation:
        raise HTTPException(status_code=404, detail="Simulation not found")

    # Capture IDs for use inside generators (session-independent scalars)
    sim_id = simulation.id
    config_id = simulation.configuration_id
    tenant_id = tenant.tenant_id

    # If already complete, replay stored steps via a fresh session
    if simulation.status in ("passed", "failed"):

        async def replay_generator() -> AsyncGenerator[str, None]:
            async with async_session_factory() as fresh_db:
                steps_stmt = (
                    select(SimulationStep)
                    .where(SimulationStep.simulation_id == sim_id)
                    .order_by(SimulationStep.step_order)
                )
                steps_result = await fresh_db.execute(steps_stmt)
                stored_steps = list(steps_result.scalars().all())

                for step in stored_steps:
                    yield f"event: step\ndata: {json.dumps(_serialize_step(step))}\n\n"
                yield f'event: done\ndata: {{"total_steps": {len(stored_steps)}}}\n\n'

        return StreamingResponse(
            replay_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Run fresh -- fetch configuration to validate before streaming
    cfg_stmt = select(Configuration).where(
        Configuration.id == config_id,
        Configuration.tenant_id == tenant_id,
    )
    cfg_result = await db.execute(cfg_stmt)
    config = cfg_result.scalar_one_or_none()
    if not config or not config.full_config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    full_config = json.loads(config.full_config)
    test_type = simulation.test_type

    async def run_and_stream() -> AsyncGenerator[str, None]:
        step_index = 0
        collected: list[SimulationStepResult] = []
        try:
            async for step in simulator.run_simulation_stream_async(
                full_config, test_type=test_type
            ):
                collected.append(step)
                yield f"event: step\ndata: {json.dumps(step.model_dump())}\n\n"
                step_index += 1
        except Exception as exc:
            error_data = json.dumps({"message": str(exc), "steps_completed": step_index})
            yield f"event: error\ndata: {error_data}\n\n"
            # Persist error status in a fresh session
            async with async_session_factory() as err_db:
                err_result = await err_db.execute(
                    select(Simulation).where(Simulation.id == sim_id)
                )
                err_simulation = err_result.scalar_one()
                err_simulation.status = "error"
                err_simulation.error_log = str(exc)
                await err_db.commit()
            return

        # Persist results to DB using a fresh session
        async with async_session_factory() as fresh_db:
            result = await fresh_db.execute(
                select(Simulation).where(Simulation.id == sim_id)
            )
            sim_row = result.scalar_one()

            total = len(collected)
            passed_count = sum(1 for s in collected if s.status == "passed")
            failed_count = total - passed_count
            total_duration = sum(s.duration_ms for s in collected)

            sim_row.status = "passed" if failed_count == 0 else "failed"
            sim_row.total_tests = total
            sim_row.passed_tests = passed_count
            sim_row.failed_tests = failed_count
            sim_row.duration_ms = total_duration
            sim_row.results = json.dumps([s.model_dump() for s in collected])

            for i, step in enumerate(collected):
                sim_step = SimulationStep(
                    simulation_id=sim_id,
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
                fresh_db.add(sim_step)

            await fresh_db.commit()

        yield f'event: done\ndata: {{"total_steps": {step_index}}}\n\n'

    return StreamingResponse(
        run_and_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
