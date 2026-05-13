"""Integration tests for the composite validate-and-test endpoint's replay
short-circuit (issue #119).

When the config is already in the `testing` lifecycle state, both internal
transitions report ``skipped``. In that situation the endpoint must NOT run a
fresh smoke simulation — it must reuse the most recent passed Simulation for
the same configuration_id + test_type so replays are idempotent and don't flap
on LLM nondeterminism.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.adapter import Adapter, AdapterVersion
from finspark.models.configuration import Configuration
from finspark.models.simulation import Simulation, SimulationStep
from finspark.schemas.common import ConfigStatus


async def _make_adapter_version(db: AsyncSession) -> AdapterVersion:
    adapter = Adapter(name="Replay eKYC", category="kyc", description="for tests")
    db.add(adapter)
    await db.flush()
    av = AdapterVersion(
        adapter_id=adapter.id, version="v1", auth_type="api_key", version_order=1
    )
    db.add(av)
    await db.flush()
    return av


async def _make_config_in_testing(db: AsyncSession, tenant_id: str = "test-tenant") -> Configuration:
    """Persist a Configuration already in the `testing` lifecycle state."""
    av = await _make_adapter_version(db)
    cfg = Configuration(
        tenant_id=tenant_id,
        name="Replay Config",
        adapter_version_id=av.id,
        document_id=None,
        status=ConfigStatus.TESTING.value,
        version=1,
        full_config=json.dumps(
            {
                "base_url": "https://api.example.com/v1",
                "auth": {"type": "api_key"},
                "endpoints": [{"id": "verify", "path": "/verify", "method": "POST"}],
                "field_mappings": [
                    {
                        "source_field": "aadhaar",
                        "target_field": "aadhaar_number",
                        "transformation": None,
                        "confidence": 1.0,
                    }
                ],
            }
        ),
    )
    db.add(cfg)
    await db.flush()
    return cfg


async def _make_prior_passed_smoke(
    db: AsyncSession, cfg_id: str, tenant_id: str = "test-tenant"
) -> Simulation:
    """Persist a prior passed smoke Simulation with three step records."""
    sim = Simulation(
        tenant_id=tenant_id,
        configuration_id=cfg_id,
        status="passed",
        test_type="smoke",
        total_tests=3,
        passed_tests=3,
        failed_tests=0,
        duration_ms=1234,
        results=json.dumps([]),
    )
    db.add(sim)
    await db.flush()

    for i, name in enumerate(["auth_handshake", "happy_path", "error_envelope"]):
        db.add(
            SimulationStep(
                simulation_id=sim.id,
                step_name=name,
                step_order=i,
                status="passed",
                request_payload="{}",
                expected_response="{}",
                actual_response="{}",
                duration_ms=400,
            )
        )
    await db.flush()
    return sim


@pytest.mark.asyncio
async def test_replay_reuses_prior_passed_smoke(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Config in `testing` + prior passed smoke → reuse, don't re-run."""
    cfg = await _make_config_in_testing(db_session)
    prior = await _make_prior_passed_smoke(db_session, cfg.id)

    # Hit the composite endpoint with an empty body
    resp = await client.post(
        f"/api/v1/configurations/{cfg.id}/validate-and-test",
        json={},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    # Overall verdict reflects the prior run
    assert data["overall_status"] == "passed"
    assert data["final_state"] == "testing"
    assert data["simulation_id"] == prior.id
    assert data["passed_tests"] == 3
    assert data["total_tests"] == 3
    assert data["failed_tests"] == 0

    # Both lifecycle transitions report skipped
    steps_by_name = {s["name"]: s for s in data["steps"]}
    assert steps_by_name["transition_to_validating"]["status"] == "skipped"
    assert steps_by_name["transition_to_testing"]["status"] == "skipped"

    # The smoke step is the replay summary, NOT a freshly-created sim
    smoke = steps_by_name["smoke_simulation"]
    assert smoke["status"] == "passed"
    assert smoke["details"]["simulation_id"] == prior.id
    assert smoke["details"]["replayed_from_prior_run"] is True
    assert smoke["details"]["step_names"] == [
        "auth_handshake",
        "happy_path",
        "error_envelope",
    ]


@pytest.mark.asyncio
async def test_replay_runs_fresh_when_no_prior_smoke(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Config in `testing` but never had a passed smoke → run fresh."""
    cfg = await _make_config_in_testing(db_session)
    # No prior Simulation persisted.

    resp = await client.post(
        f"/api/v1/configurations/{cfg.id}/validate-and-test",
        json={},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    # A new simulation was created (id present, not pointing at any prior one)
    assert data["simulation_id"] is not None
    smoke = next(s for s in data["steps"] if s["name"] == "smoke_simulation")
    # Either fresh-run or first run — must NOT be flagged as replayed
    assert smoke["details"].get("replayed_from_prior_run") is not True


@pytest.mark.asyncio
async def test_replay_skips_when_only_failed_prior_smoke(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A previously failed smoke must not trigger replay — fresh run instead."""
    cfg = await _make_config_in_testing(db_session)
    failed = Simulation(
        tenant_id="test-tenant",
        configuration_id=cfg.id,
        status="failed",
        test_type="smoke",
        total_tests=3,
        passed_tests=1,
        failed_tests=2,
        duration_ms=900,
        results=json.dumps([]),
    )
    db_session.add(failed)
    await db_session.flush()

    resp = await client.post(
        f"/api/v1/configurations/{cfg.id}/validate-and-test",
        json={},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    smoke = next(s for s in data["steps"] if s["name"] == "smoke_simulation")
    assert smoke["details"].get("replayed_from_prior_run") is not True
    # New simulation_id, not the failed one
    assert data["simulation_id"] != failed.id
