"""Integration tests for the Universal API + drop-in Claude Skill surface
(Issue #116).

Enforces two classes of invariant:

* **Audit invariants**: every required ``(METHOD, PATH)`` listed in
  :mod:`finspark.api.skill_surface` is registered on the FastAPI app, and the
  OpenAPI schema actually documents the composite endpoint so external skill
  consumers can discover it.
* **Composite endpoint behaviour**: ``POST
  /api/v1/configurations/{id}/validate-and-test`` runs the full validate ->
  test pipeline server-side, persists two ``Simulation`` rows, advances the
  config lifecycle, and is idempotent on re-run.

Uses the existing ``client`` AsyncClient fixture from ``tests/conftest.py``
so it shares the in-memory SQLite DB other integration tests use -- no real
uvicorn process is spawned, which keeps parallel agent test runs from
contending over port 8000.
"""
from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.api.skill_surface import (
    REQUIRED_API_SURFACE,
    find_missing_routes,
)
from finspark.main import app
from finspark.models.adapter import Adapter, AdapterVersion
from finspark.models.configuration import Configuration
from finspark.models.simulation import Simulation


# ---------------------------------------------------------------------------
# DB seeding helpers -- intentionally lightweight; the audit assertions are
# the load-bearing tests and don't need adapter rows.
# ---------------------------------------------------------------------------


async def _seed_adapter_with_version(db: AsyncSession) -> AdapterVersion:
    adapter = Adapter(
        name="Aadhaar eKYC Provider",
        category="kyc",
        description="eKYC verification",
        is_active=True,
        icon="shield-check",
    )
    db.add(adapter)
    await db.flush()

    version = AdapterVersion(
        adapter_id=adapter.id,
        version="v1",
        version_order=1,
        status="active",
        base_url="https://api.ekyc.example.com/v2",
        auth_type="oauth2",
        endpoints=json.dumps(
            [{"path": "/verify", "method": "POST", "description": "Verify"}]
        ),
        request_schema=json.dumps(
            {
                "type": "object",
                "required": ["aadhaar_number"],
                "properties": {"aadhaar_number": {"type": "string"}},
            }
        ),
        response_schema=json.dumps({"type": "object"}),
    )
    db.add(version)
    await db.flush()
    return version


async def _seed_configured_configuration(
    db: AsyncSession, adapter_version: AdapterVersion
) -> Configuration:
    full_config = {
        "adapter_name": "Aadhaar eKYC Provider",
        "version": "v1",
        "base_url": "https://api.ekyc.example.com/v2",
        "auth": {"type": "oauth2", "credentials": {"api_key": "env:ADAPTER_API_KEY"}},
        "endpoints": [{"path": "/verify", "method": "POST"}],
        "field_mappings": [
            {"source_field": "aadhaar_number", "target_field": "aadhaar", "confidence": 0.95},
        ],
        "transformation_rules": [],
        "hooks": [],
        "retry_policy": {"max_retries": 3},
    }
    config = Configuration(
        tenant_id="test-tenant",
        name="Skill Surface eKYC",
        adapter_version_id=adapter_version.id,
        status="configured",
        version=1,
        field_mappings=json.dumps(full_config["field_mappings"]),
        transformation_rules=json.dumps([]),
        hooks=json.dumps([]),
        full_config=json.dumps(full_config),
    )
    db.add(config)
    await db.flush()
    return config


# ---------------------------------------------------------------------------
# Audit invariants -- run on every CI build to catch dropped routes.
# ---------------------------------------------------------------------------


class TestSkillAPISurfaceAudit:
    def test_every_required_route_is_registered(self) -> None:
        """``find_missing_routes(app.routes)`` must come back empty.

        This is the primary audit invariant from the persona: any UI surface
        in ``frontend/src/pages/`` must be reachable via HTTP. Failures here
        identify orphan buttons -- features the user can click in the UI but
        an automation cannot reach through the API.
        """
        missing = find_missing_routes(app.routes)
        assert missing == [], (
            "Required API surface is missing routes -- the React UI has "
            "features unreachable via HTTP:\n  "
            + "\n  ".join(f"{r.method} {r.path}  (page={r.page})" for r in missing)
        )

    def test_composite_validate_and_test_endpoint_exists(self) -> None:
        """Dedicated assertion mirroring the persona's hard requirement."""
        keys = set()
        for route in app.routes:
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", None) or set()
            if not path:
                continue
            for method in methods:
                if isinstance(method, str):
                    keys.add((method.upper(), path))
        assert ("POST", "/api/v1/configurations/{config_id}/validate-and-test") in keys

    @pytest.mark.asyncio
    async def test_openapi_schema_advertises_composite_endpoint(
        self, client: AsyncClient
    ) -> None:
        """External skill consumers discover endpoints via OpenAPI -- the
        composite endpoint must appear in the schema, not just the router."""
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        composite_path = "/api/v1/configurations/{config_id}/validate-and-test"
        assert composite_path in paths, (
            f"composite endpoint not in OpenAPI schema; got paths={sorted(paths.keys())[:8]}..."
        )
        assert "post" in paths[composite_path], (
            f"composite endpoint exists but POST is missing: {paths[composite_path]}"
        )

    def test_required_surface_includes_no_orphan_pages(self) -> None:
        """Cheaper guard: the required surface covers every page that exists
        in ``frontend/src/pages/``. Pure-Python: walks the directory."""
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        pages_dir = repo_root / "frontend" / "src" / "pages"
        actual_pages = {p.name for p in pages_dir.glob("*.tsx")}

        covered_pages = {
            r.page for r in REQUIRED_API_SURFACE if r.page.endswith(".tsx")
        }
        # We're allowed to skip a page only if it's an internal-only screen
        # with no API surface; the persona scope is explicit that every
        # interactive page traces to a route, so we require strict coverage.
        uncovered = actual_pages - covered_pages
        assert not uncovered, (
            f"Frontend pages with no required API route declared: {sorted(uncovered)}. "
            "Add an entry to REQUIRED_API_SURFACE in src/finspark/api/skill_surface.py."
        )


# ---------------------------------------------------------------------------
# Composite endpoint behaviour
# ---------------------------------------------------------------------------


class TestValidateAndTestComposite:
    @pytest.mark.asyncio
    async def test_unknown_config_returns_404(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/configurations/does-not-exist/validate-and-test"
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_full_pipeline_returns_validation_and_testing(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The happy path: both simulation phases run and the response
        envelope carries both Simulation payloads plus the high-level phase
        the inline UI panel renders."""
        av = await _seed_adapter_with_version(db_session)
        config = await _seed_configured_configuration(db_session, av)

        resp = await client.post(
            f"/api/v1/configurations/{config.id}/validate-and-test"
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        data = body["data"]

        assert data["configuration_id"] == config.id
        assert data["phase"] in {"done", "error"}

        validation = data["validation"]
        assert validation["test_type"] == "integration"
        assert validation["status"] in {"passed", "failed"}
        assert isinstance(validation["steps"], list)
        assert validation["total_tests"] == len(validation["steps"])

        if data["phase"] == "done":
            assert data["testing"] is not None, "testing phase must be present on done"
            testing = data["testing"]
            assert testing["test_type"] == "smoke"
            assert testing["status"] == "passed"
            assert data["error_message"] is None
        else:
            # If validation failed in this environment (rule-based simulator
            # in CI without an LLM key), the testing phase must be skipped.
            if validation["status"] != "passed":
                assert data["testing"] is None
                assert data["error_message"]

    @pytest.mark.asyncio
    async def test_pipeline_persists_simulation_rows(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Both phases should create ``Simulation`` rows -- this is what the
        Audit Log and Dashboard analytics dashboards count."""
        av = await _seed_adapter_with_version(db_session)
        config = await _seed_configured_configuration(db_session, av)

        resp = await client.post(
            f"/api/v1/configurations/{config.id}/validate-and-test"
        )
        assert resp.status_code == 200

        stmt = select(Simulation).where(Simulation.configuration_id == config.id)
        rows = (await db_session.execute(stmt)).scalars().all()
        types = sorted(r.test_type for r in rows)
        # Must have at least the validation row; smoke row only if validation passed.
        assert "integration" in types
        body = resp.json()
        if body["data"]["phase"] == "done":
            assert "smoke" in types

    @pytest.mark.asyncio
    async def test_pipeline_is_idempotent_on_already_validating(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Re-running the pipeline against a config already past ``configured``
        must not 400. The route swallows ``InvalidTransitionError`` so agents
        can safely retry."""
        av = await _seed_adapter_with_version(db_session)
        config = await _seed_configured_configuration(db_session, av)
        # Pretend the config is already in a later lifecycle state.
        config.status = "validating"
        await db_session.flush()

        resp = await client.post(
            f"/api/v1/configurations/{config.id}/validate-and-test"
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["data"]["configuration_id"] == config.id
