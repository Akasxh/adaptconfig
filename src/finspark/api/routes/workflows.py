"""Workflow orchestration API routes."""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.api.dependencies import get_tenant_context
from finspark.core.database import get_db
from finspark.models.workflow import Workflow, WorkflowRun
from finspark.schemas.common import APIResponse, TenantContext
from finspark.schemas.workflows import (
    WorkflowCreate,
    WorkflowResponse,
    WorkflowRunCreate,
    WorkflowRunEventRequest,
    WorkflowRunResponse,
    WorkflowStepLogResponse,
)
from finspark.services.orchestration.engine import WorkflowEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workflows", tags=["workflows"])


def _workflow_to_response(wf: Workflow) -> WorkflowResponse:
    """Convert a Workflow ORM object to a response schema."""
    return WorkflowResponse(
        id=wf.id,
        name=wf.name,
        version=wf.version,
        description=wf.description,
        definition=json.loads(wf.definition),
        timeout_seconds=wf.timeout_seconds,
        max_total_steps=wf.max_total_steps,
        fuel_budget=wf.fuel_budget,
        status=wf.status,
        created_at=wf.created_at,
        updated_at=wf.updated_at,
    )


def _run_to_response(run: WorkflowRun) -> WorkflowRunResponse:
    """Convert a WorkflowRun ORM object to a response schema."""
    return WorkflowRunResponse(
        id=run.id,
        workflow_id=run.workflow_id,
        current_node=run.current_node,
        status=run.status,
        context=json.loads(run.context) if isinstance(run.context, str) else run.context,
        visit_counts=json.loads(run.visit_counts) if isinstance(run.visit_counts, str) else run.visit_counts,
        steps_taken=run.steps_taken,
        fuel_remaining=run.fuel_remaining,
        started_at=run.started_at,
        completed_at=run.completed_at,
        terminal_reason=run.terminal_reason,
    )


@router.post("/", response_model=APIResponse[WorkflowResponse])
async def create_workflow(
    body: WorkflowCreate,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[WorkflowResponse]:
    """Create a new workflow definition."""
    engine = WorkflowEngine(db)
    try:
        wf = await engine.create_workflow(
            tenant_id=tenant.tenant_id,
            name=body.name,
            definition=body.definition,
            version=body.version,
            description=body.description,
            timeout_seconds=body.timeout_seconds,
            max_total_steps=body.max_total_steps,
            fuel_budget=body.fuel_budget,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return APIResponse(
        data=_workflow_to_response(wf),
        message="Workflow created",
    )


@router.get("/", response_model=APIResponse[list[WorkflowResponse]])
async def list_workflows(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[list[WorkflowResponse]]:
    """List all workflows for the current tenant."""
    stmt = (
        select(Workflow)
        .where(Workflow.tenant_id == tenant.tenant_id)
        .order_by(Workflow.created_at.desc())
    )
    result = await db.execute(stmt)
    workflows = result.scalars().all()
    return APIResponse(
        data=[_workflow_to_response(wf) for wf in workflows],
    )


@router.get("/{workflow_id}", response_model=APIResponse[WorkflowResponse])
async def get_workflow(
    workflow_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[WorkflowResponse]:
    """Get workflow details."""
    stmt = select(Workflow).where(
        Workflow.id == workflow_id,
        Workflow.tenant_id == tenant.tenant_id,
    )
    result = await db.execute(stmt)
    wf = result.scalar_one_or_none()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")

    return APIResponse(data=_workflow_to_response(wf))


@router.post(
    "/{workflow_id}/start",
    response_model=APIResponse[WorkflowRunResponse],
)
async def start_workflow_run(
    workflow_id: str,
    body: WorkflowRunCreate,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[WorkflowRunResponse]:
    """Start an async workflow run."""
    engine = WorkflowEngine(db)
    try:
        run = await engine.start_run(
            workflow_id=workflow_id,
            tenant_id=tenant.tenant_id,
            initial_context=body.initial_context,
            callback_url=body.callback_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return APIResponse(
        data=_run_to_response(run),
        message=f"Workflow run {run.status}",
    )


@router.post(
    "/{workflow_id}/run",
    response_model=APIResponse[WorkflowRunResponse],
)
async def run_workflow_sync(
    workflow_id: str,
    body: WorkflowRunCreate,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[WorkflowRunResponse]:
    """Run a workflow synchronously (blocks until terminal or timeout)."""
    engine = WorkflowEngine(db)
    try:
        run = await engine.start_run(
            workflow_id=workflow_id,
            tenant_id=tenant.tenant_id,
            initial_context=body.initial_context,
            callback_url=body.callback_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return APIResponse(
        data=_run_to_response(run),
        message=f"Workflow run {run.status}",
    )


@router.get(
    "/runs/{run_id}",
    response_model=APIResponse[WorkflowRunResponse],
)
async def get_run_status(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[WorkflowRunResponse]:
    """Get current state of a workflow run."""
    engine = WorkflowEngine(db)
    try:
        run = await engine.get_run_status(run_id, tenant.tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return APIResponse(data=_run_to_response(run))


@router.post(
    "/runs/{run_id}/resume",
    response_model=APIResponse[WorkflowRunResponse],
)
async def resume_run(
    run_id: str,
    body: WorkflowRunEventRequest,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[WorkflowRunResponse]:
    """Resume a paused workflow run with event data."""
    engine = WorkflowEngine(db)
    try:
        run = await engine.resume_run(
            run_id=run_id,
            tenant_id=tenant.tenant_id,
            event_data=body.event_data,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return APIResponse(
        data=_run_to_response(run),
        message=f"Workflow run {run.status}",
    )


@router.get(
    "/runs/{run_id}/steps",
    response_model=APIResponse[list[WorkflowStepLogResponse]],
)
async def get_run_steps(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[list[WorkflowStepLogResponse]]:
    """Get step log for a workflow run."""
    engine = WorkflowEngine(db)
    logs = await engine.get_step_logs(run_id)

    return APIResponse(
        data=[
            WorkflowStepLogResponse(
                id=log.id,
                run_id=log.run_id,
                node_id=log.node_id,
                node_type=log.node_type,
                status=log.status,
                input_snapshot=json.loads(log.input_snapshot) if log.input_snapshot else None,
                output_snapshot=json.loads(log.output_snapshot) if log.output_snapshot else None,
                duration_ms=log.duration_ms,
                error=log.error,
                transition_to=log.transition_to,
                created_at=log.created_at,
            )
            for log in logs
        ],
    )
