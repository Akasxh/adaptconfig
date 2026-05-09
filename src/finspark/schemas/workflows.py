"""Pydantic schemas for workflow CRUD and run management."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class WorkflowCreate(BaseModel):
    """Create a new workflow definition."""

    name: str
    version: str = "1.0"
    description: str | None = None
    definition: dict[str, Any]
    timeout_seconds: int = 86400
    max_total_steps: int = 500
    fuel_budget: int = 1000


class WorkflowResponse(BaseModel):
    """Workflow definition response."""

    id: str
    name: str
    version: str
    description: str | None = None
    definition: dict[str, Any]
    timeout_seconds: int
    max_total_steps: int
    fuel_budget: int
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class WorkflowRunCreate(BaseModel):
    """Start a new workflow run."""

    initial_context: dict[str, Any] = {}
    callback_url: str | None = None


class WorkflowRunResponse(BaseModel):
    """Workflow run status response."""

    id: str
    workflow_id: str
    current_node: str
    status: str
    context: dict[str, Any]
    visit_counts: dict[str, int]
    steps_taken: int
    fuel_remaining: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    terminal_reason: str | None = None

    model_config = ConfigDict(from_attributes=True)


class WorkflowRunEventRequest(BaseModel):
    """Event data for resuming a paused workflow run."""

    event_type: str = "resume"
    event_data: dict[str, Any] = {}


class WorkflowStepLogResponse(BaseModel):
    """Step log entry response."""

    id: str
    run_id: str
    node_id: str
    node_type: str
    status: str
    input_snapshot: dict[str, Any] | None = None
    output_snapshot: dict[str, Any] | None = None
    duration_ms: int
    error: str | None = None
    transition_to: str | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
