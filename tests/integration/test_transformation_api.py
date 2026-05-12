"""Integration tests for ``transformation_expr`` over the FastAPI surface.

These tests run against the in-process ASGI ``client`` fixture defined in
``tests/conftest.py`` so they share the same in-memory SQLite DB and don't
spin up a real uvicorn — keeping parallel test runs from fighting over a
shared port.

Coverage:

- PATCH /api/v1/configurations/{id} persists ``transformation_expr`` and
  round-trips it through GET.
- PATCH with an invalid expression still returns 200 (mapping stays
  editable per persona) and surfaces the parse error in
  ``APIResponse.errors`` and ``transformation_expr_error`` on the row.
- POST /api/v1/simulations/run does not crash when a config carries an
  invalid expression — the simulator falls back through to the enum.
- Configurations with valid ``int(x) | clamp(...)`` round-trip through
  the persisted ``field_mappings`` JSON column.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.configuration import Configuration


def _make_config(*, mappings: list[dict]) -> Configuration:
    return Configuration(
        id=str(uuid.uuid4()),
        tenant_id="test-tenant",
        name="Transformation Test Config",
        adapter_version_id=str(uuid.uuid4()),
        status="configured",
        version=1,
        field_mappings=json.dumps(mappings),
        transformation_rules=json.dumps([]),
        hooks=json.dumps(
            [
                {
                    "name": "log_request",
                    "type": "pre_request",
                    "handler": "audit_logger",
                    "is_active": True,
                }
            ]
        ),
        full_config=json.dumps(
            {
                "adapter_name": "Test Adapter",
                "version": "v1",
                "base_url": "https://api.test.example.com/v1",
                "auth": {"type": "api_key", "credentials": {"api_key": "mock"}},
                "endpoints": [{"path": "/verify", "method": "POST", "enabled": True}],
                "field_mappings": mappings,
                "hooks": [
                    {
                        "name": "log_request",
                        "type": "pre_request",
                        "handler": "audit_logger",
                        "is_active": True,
                    }
                ],
                "retry_policy": {
                    "max_retries": 3,
                    "backoff_factor": 2,
                    "retry_on_status": [429, 500, 502, 503],
                },
                "timeout_ms": 30000,
            }
        ),
    )


@pytest.mark.asyncio
class TestTransformationExprPersistence:
    async def test_patch_persists_valid_expr(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        config = _make_config(
            mappings=[
                {
                    "source_field": "loan_amount",
                    "target_field": "amount",
                    "transformation": None,
                    "transformation_expr": None,
                    "confidence": 0.9,
                    "is_confirmed": True,
                }
            ]
        )
        db_session.add(config)
        await db_session.flush()

        body = {
            "field_mappings": [
                {
                    "source_field": "loan_amount",
                    "target_field": "amount",
                    "transformation": None,
                    "transformation_expr": "int(x) | clamp(0, 1_000_000)",
                    "confidence": 0.9,
                    "is_confirmed": True,
                }
            ]
        }
        resp = await client.patch(f"/api/v1/configurations/{config.id}", json=body)
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["success"] is True
        assert payload["errors"] == []
        mappings = payload["data"]["field_mappings"]
        assert len(mappings) == 1
        assert mappings[0]["transformation_expr"] == "int(x) | clamp(0, 1_000_000)"
        assert mappings[0]["transformation_expr_error"] is None

        # GET round-trip: the expression must survive serialization.
        get_resp = await client.get(f"/api/v1/configurations/{config.id}")
        assert get_resp.status_code == 200
        round_tripped = get_resp.json()["data"]["field_mappings"][0]
        assert round_tripped["transformation_expr"] == "int(x) | clamp(0, 1_000_000)"
        assert round_tripped["transformation_expr_error"] is None

    async def test_patch_persists_invalid_expr_with_inline_error(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Per persona: invalid expr → mapping stays editable, sim doesn't crash,
        UI shows red message inline. We surface the parse error both in the
        flat ``errors`` channel and per-row via ``transformation_expr_error``.
        """
        config = _make_config(
            mappings=[
                {
                    "source_field": "pan_number",
                    "target_field": "pan",
                    "transformation": "upper",
                    "transformation_expr": None,
                    "confidence": 1.0,
                    "is_confirmed": True,
                }
            ]
        )
        db_session.add(config)
        await db_session.flush()

        body = {
            "field_mappings": [
                {
                    "source_field": "pan_number",
                    "target_field": "pan",
                    "transformation": "upper",
                    "transformation_expr": "eval(x)",
                    "confidence": 1.0,
                    "is_confirmed": True,
                }
            ]
        }
        resp = await client.patch(f"/api/v1/configurations/{config.id}", json=body)
        assert resp.status_code == 200, resp.text
        payload = resp.json()

        # The row is still persisted (editable) — the bad expr round-trips.
        mappings = payload["data"]["field_mappings"]
        assert mappings[0]["transformation_expr"] == "eval(x)"
        # Inline error string for the UI.
        assert mappings[0]["transformation_expr_error"] is not None
        assert "eval" in mappings[0]["transformation_expr_error"]

        # Flat APIResponse.errors lists the bad expression once.
        assert len(payload["errors"]) == 1
        assert payload["errors"][0].startswith("field_mappings[0].transformation_expr")
        assert "invalid expression" in payload["message"].lower()

        # The PATCH did NOT reject — GET also reflects the persisted bad expr
        # and its error annotation, so the user can correct it in place.
        get_resp = await client.get(f"/api/v1/configurations/{config.id}")
        assert get_resp.status_code == 200
        get_mapping = get_resp.json()["data"]["field_mappings"][0]
        assert get_mapping["transformation_expr"] == "eval(x)"
        assert get_mapping["transformation_expr_error"] is not None

    async def test_patch_clearing_expr_removes_inline_error(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        config = _make_config(
            mappings=[
                {
                    "source_field": "pan_number",
                    "target_field": "pan",
                    "transformation": "upper",
                    "transformation_expr": "eval(x)",
                    "confidence": 1.0,
                    "is_confirmed": True,
                }
            ]
        )
        db_session.add(config)
        await db_session.flush()

        body = {
            "field_mappings": [
                {
                    "source_field": "pan_number",
                    "target_field": "pan",
                    "transformation": "upper",
                    "transformation_expr": "",
                    "confidence": 1.0,
                    "is_confirmed": True,
                }
            ]
        }
        resp = await client.patch(f"/api/v1/configurations/{config.id}", json=body)
        assert resp.status_code == 200
        mappings = resp.json()["data"]["field_mappings"]
        assert mappings[0]["transformation_expr"] == ""
        assert mappings[0]["transformation_expr_error"] is None
        assert resp.json()["errors"] == []

    async def test_simulation_run_with_invalid_expr_does_not_crash(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Acceptance: 'sim doesn't crash (falls back to enum)' on bad exprs."""
        full_config = {
            "adapter_name": "CIBIL Credit Bureau",
            "version": "v1",
            "base_url": "https://api.cibil.com/v1",
            "auth": {"type": "api_key", "credentials": {"api_key": "mock"}},
            "endpoints": [{"path": "/credit-score", "method": "POST", "enabled": True}],
            "field_mappings": [
                {
                    "source_field": "pan_number",
                    "target_field": "pan",
                    # Invalid expr — must be tolerated.
                    "transformation_expr": "eval(x)",
                    # Enum fallback exercises the enum path.
                    "transformation": "upper",
                    "confidence": 1.0,
                },
                {
                    "source_field": "loan_amount",
                    "target_field": "amount",
                    "transformation_expr": "int(x) | clamp(0, 1_000_000)",
                    "transformation": None,
                    "confidence": 0.95,
                },
            ],
            "transformation_rules": [],
            "hooks": [
                {
                    "name": "log_request",
                    "type": "pre_request",
                    "handler": "audit_logger",
                    "is_active": True,
                }
            ],
            "retry_policy": {
                "max_retries": 3,
                "backoff_factor": 2,
                "retry_on_status": [429, 500, 502, 503],
            },
            "timeout_ms": 30000,
        }
        config = Configuration(
            id=str(uuid.uuid4()),
            tenant_id="test-tenant",
            name="Bad Expr Sim Config",
            adapter_version_id=str(uuid.uuid4()),
            status="configured",
            version=1,
            field_mappings=json.dumps(full_config["field_mappings"]),
            transformation_rules=json.dumps([]),
            hooks=json.dumps(full_config["hooks"]),
            full_config=json.dumps(full_config),
        )
        db_session.add(config)
        await db_session.flush()

        # Force the rule-based simulation path — ai_enabled gates which
        # branch runs in /simulations/run, and the rule-based branch is
        # the one that exercises ``_build_sample_request`` end-to-end.
        with patch("finspark.api.routes.simulations.settings") as mock_settings:
            mock_settings.ai_enabled = False
            mock_settings.gemini_api_key = ""
            resp = await client.post(
                "/api/v1/simulations/run",
                json={"configuration_id": config.id, "test_type": "smoke"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        # Simulation completed end-to-end. We don't pin overall status (the
        # mock CIBIL response shape may pass or fail field_mapping_validation
        # depending on coverage), but the request itself must not crash and
        # the endpoint test step must run with a request_payload built from
        # the bad+good mappings.
        assert body["configuration_id"] == config.id
        assert isinstance(body["steps"], list)
        endpoint_steps = [
            s for s in body["steps"] if s["step_name"].startswith("endpoint_test_")
        ]
        assert endpoint_steps, "Expected at least one endpoint_test_ step"
        sample_request = endpoint_steps[0]["request_payload"]
        # Bad expr fell back to enum 'upper'; pan mock is already upper.
        assert sample_request["pan_number"] == "ABCDE1234F"
        # Valid expr clamped the loan amount to <= 1_000_000.
        assert sample_request["loan_amount"] == 500_000

    async def test_patch_get_omits_legacy_field_mappings_without_error_field(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Existing rows without ``transformation_expr`` keep working unchanged."""
        config = _make_config(
            mappings=[
                # Legacy shape — no transformation_expr key at all.
                {
                    "source_field": "pan_number",
                    "target_field": "pan",
                    "transformation": "upper",
                    "confidence": 1.0,
                    "is_confirmed": True,
                }
            ]
        )
        db_session.add(config)
        await db_session.flush()

        get_resp = await client.get(f"/api/v1/configurations/{config.id}")
        assert get_resp.status_code == 200
        m = get_resp.json()["data"]["field_mappings"][0]
        # Legacy enum still present.
        assert m["transformation"] == "upper"
        # New field defaults to null and has no error.
        assert m.get("transformation_expr") is None
        assert m.get("transformation_expr_error") is None
