"""In-process mirror of `scripts/skill_smoke.py`.

Exercises the workflow documented in `adaptconfig.skill.md` against the
ASGI app via FastAPI TestClient (no live backend required).  The
assertions match the smoke driver: the composite endpoint must report
exactly 7/7 passing smoke tests for the gold-standard fixture, and
every step in the pipeline must be either ``passed`` or ``skipped``.

This file uses *only* endpoints listed in `adaptconfig.skill.md`, so a
passing run is positive evidence that the Skill schema is sufficient
for a third-party agent to drive AdaptConfig end-to-end.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.adapter import Adapter, AdapterVersion

FIXTURE = (
    Path(__file__).parent.parent.parent / "test_fixtures" / "05_perfect_kyc_api.yaml"
)


@pytest.fixture(autouse=True)
def _disable_external_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the smoke deterministic: no LLM call, no webhook fan-out."""

    async def _noop(*_args, **_kwargs):  # noqa: ANN001
        return None

    async def _raise(*_args, **_kwargs):  # noqa: ANN001
        raise RuntimeError("LLM disabled in skill smoke tests")

    monkeypatch.setattr(
        "finspark.api.routes.configurations.deliver_event", _noop
    )
    monkeypatch.setattr(
        "finspark.api.routes.documents.deliver_event", _noop, raising=False
    )
    monkeypatch.setattr(
        "finspark.api.routes.simulations.deliver_event", _noop, raising=False
    )
    monkeypatch.setattr(
        "finspark.api.routes.configurations.generate_config_llm",
        _raise,
        raising=False,
    )


async def _seed_three_endpoint_adapter(db_session: AsyncSession) -> str:
    """Create a generic 3-endpoint adapter so the smoke produces 7/7.

    See `tests/integration/test_skill_api_surface.py` for rationale on the
    generic adapter name + base_url.
    """
    adapter = Adapter(
        name="AdaptConfig Generic Verification",
        category="custom",
        description="Three-endpoint generic adapter for the skill smoke driver.",
        is_active=True,
        icon="shield-check",
    )
    db_session.add(adapter)
    await db_session.flush()

    av = AdapterVersion(
        adapter_id=adapter.id,
        version="v1",
        base_url="https://api.adaptconfig.example.com/v1",
        auth_type="api_key",
        endpoints=json.dumps(
            [
                {"path": "/verify/aadhaar", "method": "POST"},
                {"path": "/verify/pan", "method": "POST"},
                {"path": "/verify/status/{reference_id}", "method": "GET"},
            ]
        ),
        request_schema=json.dumps(
            {
                "type": "object",
                "required": ["aadhaar_number", "customer_name", "consent_id"],
                "properties": {
                    "aadhaar_number": {"type": "string"},
                    "pan_number": {"type": "string"},
                    "customer_name": {"type": "string"},
                    "consent_id": {"type": "string"},
                },
            }
        ),
        response_schema=json.dumps(
            {
                "type": "object",
                "properties": {
                    "verified": {"type": "boolean"},
                    "reference_id": {"type": "string"},
                },
            }
        ),
    )
    db_session.add(av)
    await db_session.flush()
    return av.id


@pytest.mark.asyncio
async def test_skill_smoke_end_to_end(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Upload -> mint adapter -> generate config -> validate-and-test = 7/7."""
    assert FIXTURE.exists(), f"missing fixture: {FIXTURE}"
    av_id = await _seed_three_endpoint_adapter(db_session)

    # 1. Upload the gold-standard fixture (skill step 1).
    with FIXTURE.open("rb") as f:
        upload = await client.post(
            "/api/v1/documents/upload",
            params={"doc_type": "api_spec"},
            files={"file": (FIXTURE.name, f, "application/x-yaml")},
        )
    assert upload.status_code == 200, upload.text
    doc_id = upload.json()["data"]["id"]

    # 2. Generate a configuration (skill step 3 — we skip step 2
    #    `from-document` because tests pre-seed an explicit adapter
    #    version to keep the assertion focused on the composite endpoint).
    gen = await client.post(
        "/api/v1/configurations/generate",
        json={
            "document_id": doc_id,
            "adapter_version_id": av_id,
            "name": "Skill Smoke Config",
        },
    )
    assert gen.status_code == 200, gen.text
    config_id = gen.json()["data"]["id"]

    # 3. The composite endpoint — the centrepiece of the Skill.
    resp = await client.post(
        f"/api/v1/configurations/{config_id}/validate-and-test",
        json={"test_type": "smoke", "reason": "skill smoke test"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["overall_status"] == "passed", data
    assert data["total_tests"] == 7, data
    assert data["passed_tests"] == 7, data
    assert data["failed_tests"] == 0
    assert data["final_state"] == "testing"
    assert data["simulation_id"]

    step_statuses = {step["name"]: step["status"] for step in data["steps"]}
    # Every pipeline step is either "passed" or "skipped" — never "failed".
    for name, status in step_statuses.items():
        assert status in {"passed", "skipped"}, (name, status)
    # The four canonical steps must be present.
    for required in (
        "transition_to_validating",
        "validate",
        "transition_to_testing",
        "smoke_simulation",
    ):
        assert required in step_statuses, step_statuses


@pytest.mark.asyncio
async def test_skill_smoke_reports_simulation_via_documented_route(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The simulation_id returned by the composite endpoint must be
    retrievable through `GET /api/v1/simulations/{id}` — i.e. the Skill's
    own `simulations` reference works against the composite output."""
    av_id = await _seed_three_endpoint_adapter(db_session)

    with FIXTURE.open("rb") as f:
        upload = await client.post(
            "/api/v1/documents/upload",
            params={"doc_type": "api_spec"},
            files={"file": (FIXTURE.name, f, "application/x-yaml")},
        )
    doc_id = upload.json()["data"]["id"]
    gen = await client.post(
        "/api/v1/configurations/generate",
        json={
            "document_id": doc_id,
            "adapter_version_id": av_id,
            "name": "Skill Smoke Lookup",
        },
    )
    config_id = gen.json()["data"]["id"]

    pipeline = await client.post(
        f"/api/v1/configurations/{config_id}/validate-and-test",
        json={"test_type": "smoke"},
    )
    sim_id = pipeline.json()["data"]["simulation_id"]
    assert sim_id

    detail = await client.get(f"/api/v1/simulations/{sim_id}")
    assert detail.status_code == 200, detail.text
    sim = detail.json()["data"]
    assert sim["id"] == sim_id
    assert sim["status"] == "passed"
    assert sim["total_tests"] == 7
    assert sim["passed_tests"] == 7
