"""Tests for workflow API routes."""

import pytest
from httpx import AsyncClient


def _valid_definition() -> dict:
    return {
        "initial_state": "start",
        "nodes": {
            "start": {
                "type": "start",
                "transitions": [{"target": "end"}],
            },
            "end": {
                "type": "start",
                "terminal": True,
            },
        },
    }


@pytest.mark.asyncio
async def test_create_workflow(client: AsyncClient) -> None:
    """POST /api/v1/workflows/ creates a workflow."""
    resp = await client.post(
        "/api/v1/workflows/",
        json={
            "name": "test-wf",
            "definition": _valid_definition(),
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["name"] == "test-wf"
    assert data["data"]["status"] == "active"
    assert data["data"]["id"] is not None


@pytest.mark.asyncio
async def test_create_invalid_workflow(client: AsyncClient) -> None:
    """POST /api/v1/workflows/ with bad definition returns 400."""
    resp = await client.post(
        "/api/v1/workflows/",
        json={
            "name": "bad-wf",
            "definition": {
                "initial_state": "missing",
                "nodes": {},
            },
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_start_run(client: AsyncClient) -> None:
    """POST /api/v1/workflows/{id}/start runs a workflow."""
    # First create a workflow
    create_resp = await client.post(
        "/api/v1/workflows/",
        json={
            "name": "run-test",
            "definition": _valid_definition(),
        },
    )
    wf_id = create_resp.json()["data"]["id"]

    # Start a run
    run_resp = await client.post(
        f"/api/v1/workflows/{wf_id}/start",
        json={"initial_context": {"key": "value"}},
    )
    assert run_resp.status_code == 200
    data = run_resp.json()
    assert data["data"]["status"] == "completed"
    assert data["data"]["workflow_id"] == wf_id


@pytest.mark.asyncio
async def test_get_run_status(client: AsyncClient) -> None:
    """GET /api/v1/workflows/runs/{run_id} returns status."""
    # Create and run
    create_resp = await client.post(
        "/api/v1/workflows/",
        json={
            "name": "status-test",
            "definition": _valid_definition(),
        },
    )
    wf_id = create_resp.json()["data"]["id"]

    run_resp = await client.post(
        f"/api/v1/workflows/{wf_id}/start",
        json={},
    )
    run_id = run_resp.json()["data"]["id"]

    # Get status
    status_resp = await client.get(f"/api/v1/workflows/runs/{run_id}")
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["data"]["id"] == run_id
    assert data["data"]["status"] == "completed"


@pytest.mark.asyncio
async def test_list_workflows(client: AsyncClient) -> None:
    """GET /api/v1/workflows/ returns list."""
    # Create two workflows
    await client.post(
        "/api/v1/workflows/",
        json={
            "name": "wf-1",
            "definition": _valid_definition(),
        },
    )
    await client.post(
        "/api/v1/workflows/",
        json={
            "name": "wf-2",
            "definition": _valid_definition(),
        },
    )

    resp = await client.get("/api/v1/workflows/")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]) >= 2


@pytest.mark.asyncio
async def test_get_workflow_detail(client: AsyncClient) -> None:
    """GET /api/v1/workflows/{id} returns detail."""
    create_resp = await client.post(
        "/api/v1/workflows/",
        json={
            "name": "detail-test",
            "definition": _valid_definition(),
            "description": "A test workflow",
        },
    )
    wf_id = create_resp.json()["data"]["id"]

    resp = await client.get(f"/api/v1/workflows/{wf_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["name"] == "detail-test"
    assert data["data"]["description"] == "A test workflow"


@pytest.mark.asyncio
async def test_get_run_steps(client: AsyncClient) -> None:
    """GET /api/v1/workflows/runs/{run_id}/steps returns step logs."""
    create_resp = await client.post(
        "/api/v1/workflows/",
        json={
            "name": "steps-test",
            "definition": _valid_definition(),
        },
    )
    wf_id = create_resp.json()["data"]["id"]

    run_resp = await client.post(
        f"/api/v1/workflows/{wf_id}/start",
        json={},
    )
    run_id = run_resp.json()["data"]["id"]

    steps_resp = await client.get(f"/api/v1/workflows/runs/{run_id}/steps")
    assert steps_resp.status_code == 200
    data = steps_resp.json()
    assert isinstance(data["data"], list)
