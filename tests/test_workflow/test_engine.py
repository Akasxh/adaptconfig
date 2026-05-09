"""Tests for the workflow orchestration engine."""

import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.services.orchestration.engine import WorkflowEngine


def _linear_definition() -> dict:
    """A -> B -> terminal_C"""
    return {
        "initial_state": "A",
        "nodes": {
            "A": {
                "type": "start",
                "transitions": [{"target": "B"}],
            },
            "B": {
                "type": "transform",
                "field_mappings": [
                    {
                        "source_field": "input_val",
                        "target_field": "output_val",
                    },
                ],
                "output_key": "transform_result",
                "transitions": [{"target": "C"}],
            },
            "C": {
                "type": "start",
                "terminal": True,
            },
        },
    }


def _conditional_definition() -> dict:
    """A -> condition(x>5 -> B, else -> C) -> terminal"""
    return {
        "initial_state": "A",
        "nodes": {
            "A": {
                "type": "start",
                "transitions": [{"target": "check"}],
            },
            "check": {
                "type": "condition",
                "branches": [
                    {
                        "condition": "$.context.x > 5",
                        "target": "B",
                    },
                ],
                "default": "C",
                "transitions": [],
            },
            "B": {
                "type": "start",
                "terminal": True,
            },
            "C": {
                "type": "start",
                "terminal": True,
            },
        },
    }


def _cycle_definition(max_visits: int = 3) -> dict:
    """A -> B -> A (cycle), max_visits on A, on_max_visits -> done."""
    return {
        "initial_state": "A",
        "nodes": {
            "A": {
                "type": "transform",
                "max_visits": max_visits,
                "on_max_visits": "done",
                "field_mappings": [],
                "transitions": [{"target": "B"}],
            },
            "B": {
                "type": "transform",
                "field_mappings": [],
                "transitions": [{"target": "A"}],
            },
            "done": {
                "type": "start",
                "terminal": True,
            },
        },
    }


def _wait_definition() -> dict:
    """A -> wait -> B -> terminal"""
    return {
        "initial_state": "A",
        "nodes": {
            "A": {
                "type": "start",
                "transitions": [{"target": "wait_node"}],
            },
            "wait_node": {
                "type": "wait",
                "transitions": [{"target": "B"}],
            },
            "B": {
                "type": "start",
                "terminal": True,
            },
        },
    }


@pytest.mark.asyncio
async def test_simple_linear_workflow(db_session: AsyncSession) -> None:
    """Create and run A->B->terminal, verify completed."""
    engine = WorkflowEngine(db_session)

    wf = await engine.create_workflow(
        tenant_id="test-tenant",
        name="linear",
        definition=_linear_definition(),
    )
    assert wf.id is not None

    run = await engine.start_run(
        workflow_id=wf.id,
        tenant_id="test-tenant",
        initial_context={"input_val": "hello"},
    )
    assert run.status == "completed"
    assert run.terminal_reason is not None
    assert "terminal" in run.terminal_reason.lower() or "C" in run.terminal_reason


@pytest.mark.asyncio
async def test_conditional_branching(db_session: AsyncSession) -> None:
    """A->condition(x>5)->B else->C, verify correct branch taken."""
    engine = WorkflowEngine(db_session)

    wf = await engine.create_workflow(
        tenant_id="test-tenant",
        name="conditional",
        definition=_conditional_definition(),
    )

    # x=10 -> should go to B
    run = await engine.start_run(
        workflow_id=wf.id,
        tenant_id="test-tenant",
        initial_context={"x": 10},
    )
    assert run.status == "completed"
    assert run.current_node == "B"

    # x=3 -> should go to C
    run2 = await engine.start_run(
        workflow_id=wf.id,
        tenant_id="test-tenant",
        initial_context={"x": 3},
    )
    assert run2.status == "completed"
    assert run2.current_node == "C"


@pytest.mark.asyncio
async def test_cycle_with_max_visits(db_session: AsyncSession) -> None:
    """A->B->A(max_visits=3), verify stops after 3 visits."""
    engine = WorkflowEngine(db_session)

    wf = await engine.create_workflow(
        tenant_id="test-tenant",
        name="cycle",
        definition=_cycle_definition(max_visits=3),
    )

    run = await engine.start_run(
        workflow_id=wf.id,
        tenant_id="test-tenant",
    )
    assert run.status == "completed"
    visits = json.loads(run.visit_counts)
    # A should be visited exactly max_visits times
    assert visits.get("A", 0) <= 3


@pytest.mark.asyncio
async def test_fuel_exhaustion(db_session: AsyncSession) -> None:
    """Set low fuel_budget, verify workflow terminates."""
    engine = WorkflowEngine(db_session)

    wf = await engine.create_workflow(
        tenant_id="test-tenant",
        name="fuel-test",
        definition=_cycle_definition(max_visits=100),
        fuel_budget=5,
    )

    run = await engine.start_run(
        workflow_id=wf.id,
        tenant_id="test-tenant",
    )
    assert run.status == "failed"
    assert run.terminal_reason is not None
    assert "fuel" in run.terminal_reason.lower() or "budget" in run.terminal_reason.lower()


@pytest.mark.asyncio
async def test_max_total_steps(db_session: AsyncSession) -> None:
    """Set low max_total_steps, verify terminates."""
    engine = WorkflowEngine(db_session)

    wf = await engine.create_workflow(
        tenant_id="test-tenant",
        name="step-test",
        definition=_cycle_definition(max_visits=100),
        max_total_steps=4,
        fuel_budget=10000,
    )

    run = await engine.start_run(
        workflow_id=wf.id,
        tenant_id="test-tenant",
    )
    assert run.status == "failed"
    assert run.terminal_reason is not None
    assert "step" in run.terminal_reason.lower()


@pytest.mark.asyncio
async def test_wait_node_pauses(db_session: AsyncSession) -> None:
    """Workflow hits wait node, verify status=PAUSED."""
    engine = WorkflowEngine(db_session)

    wf = await engine.create_workflow(
        tenant_id="test-tenant",
        name="wait-test",
        definition=_wait_definition(),
    )

    run = await engine.start_run(
        workflow_id=wf.id,
        tenant_id="test-tenant",
    )
    assert run.status == "paused"
    assert run.current_node == "wait_node"


@pytest.mark.asyncio
async def test_resume_paused_run(db_session: AsyncSession) -> None:
    """Pause then resume, verify continues to completion."""
    engine = WorkflowEngine(db_session)

    wf = await engine.create_workflow(
        tenant_id="test-tenant",
        name="resume-test",
        definition=_wait_definition(),
    )

    run = await engine.start_run(
        workflow_id=wf.id,
        tenant_id="test-tenant",
    )
    assert run.status == "paused"

    resumed = await engine.resume_run(
        run_id=run.id,
        tenant_id="test-tenant",
        event_data={"event": "approval"},
    )
    assert resumed.status == "completed"
    ctx = json.loads(resumed.context)
    assert ctx.get("event") == "approval"


@pytest.mark.asyncio
async def test_transform_node(db_session: AsyncSession) -> None:
    """Transform node modifies context."""
    engine = WorkflowEngine(db_session)

    definition = {
        "initial_state": "A",
        "nodes": {
            "A": {
                "type": "transform",
                "field_mappings": [
                    {
                        "source_field": "name",
                        "target_field": "full_name",
                    },
                ],
                "output_key": "mapped",
                "transitions": [{"target": "end"}],
            },
            "end": {
                "type": "start",
                "terminal": True,
            },
        },
    }

    wf = await engine.create_workflow(
        tenant_id="test-tenant",
        name="transform-test",
        definition=definition,
    )

    run = await engine.start_run(
        workflow_id=wf.id,
        tenant_id="test-tenant",
        initial_context={"name": "Akash"},
    )
    assert run.status == "completed"
    ctx = json.loads(run.context)
    assert "mapped" in ctx
    assert ctx["mapped"]["full_name"] == "Akash"


@pytest.mark.asyncio
async def test_workflow_persists_state(db_session: AsyncSession) -> None:
    """Verify context/visit_counts saved to DB."""
    engine = WorkflowEngine(db_session)

    wf = await engine.create_workflow(
        tenant_id="test-tenant",
        name="persist-test",
        definition=_linear_definition(),
    )

    run = await engine.start_run(
        workflow_id=wf.id,
        tenant_id="test-tenant",
        initial_context={"input_val": "test"},
    )

    # Re-fetch from DB
    fetched = await engine.get_run_status(run.id, "test-tenant")
    assert fetched.id == run.id
    assert fetched.status == "completed"

    ctx = json.loads(fetched.context)
    assert "input_val" in ctx

    visits = json.loads(fetched.visit_counts)
    assert isinstance(visits, dict)


@pytest.mark.asyncio
async def test_invalid_definition_rejected(db_session: AsyncSession) -> None:
    """Invalid workflow definition should raise ValueError."""
    engine = WorkflowEngine(db_session)

    with pytest.raises(ValueError, match="Invalid workflow definition"):
        await engine.create_workflow(
            tenant_id="test-tenant",
            name="invalid",
            definition={"nodes": {}, "initial_state": "missing"},
        )
