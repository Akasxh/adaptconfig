"""Unit tests for the PATCH /configurations/{config_id} endpoint."""

import json
import uuid
from unittest.mock import patch

import pytest

from finspark.models.configuration import Configuration


def _make_config(name: str = "Update Test Config") -> Configuration:
    return Configuration(
        id=str(uuid.uuid4()),
        tenant_id="test-tenant",
        name=name,
        adapter_version_id=str(uuid.uuid4()),
        status="configured",
        version=1,
        field_mappings=json.dumps(
            [
                {
                    "source_field": "pan_number",
                    "target_field": "pan",
                    "transformation": "upper",
                    "confidence": 0.9,
                    "is_confirmed": True,
                },
                {
                    "source_field": "full_name",
                    "target_field": "name",
                    "transformation": None,
                    "confidence": 0.8,
                    "is_confirmed": False,
                },
            ]
        ),
        transformation_rules=json.dumps([]),
        hooks=json.dumps([]),
        full_config=json.dumps(
            {
                "base_url": "https://api.test.com/v1",
                "auth": {"type": "api_key"},
                "endpoints": [{"path": "/test", "method": "POST"}],
                "field_mappings": [],
            }
        ),
    )


@pytest.mark.asyncio
class TestConfigUpdateEndpoint:
    async def test_patch_updates_field_mappings(self, client, db_session) -> None:
        config = _make_config()
        db_session.add(config)
        await db_session.flush()

        new_mappings = [
            {
                "source_field": "pan_number",
                "target_field": "pan_id",
                "transformation": "upper",
                "confidence": 0.95,
                "is_confirmed": True,
            }
        ]

        resp = await client.patch(
            f"/api/v1/configurations/{config.id}",
            json={"field_mappings": new_mappings},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["message"] == "Configuration updated"
        returned_mappings = data["data"]["field_mappings"]
        assert len(returned_mappings) == 1
        assert returned_mappings[0]["target_field"] == "pan_id"
        assert returned_mappings[0]["source_field"] == "pan_number"

    async def test_patch_keeps_full_config_field_mappings_in_sync(
        self, client, db_session
    ) -> None:
        """Validation reads full_config, so PATCH must update both mapping stores."""
        config = _make_config()
        db_session.add(config)
        await db_session.flush()

        new_mappings = [
            {
                "source_field": "pan_number",
                "target_field": "pan_id",
                "transformation": "upper",
                "confidence": 0.95,
                "is_confirmed": True,
            }
        ]

        patch_resp = await client.patch(
            f"/api/v1/configurations/{config.id}",
            json={"field_mappings": new_mappings},
        )
        assert patch_resp.status_code == 200

        validate_resp = await client.post(f"/api/v1/configurations/{config.id}/validate")
        assert validate_resp.status_code == 200
        validation = validate_resp.json()["data"]
        assert validation["coverage_score"] == 1.0
        assert validation["unmapped_source_fields"] == []

        export_resp = await client.get(f"/api/v1/configurations/{config.id}/export")
        assert export_resp.status_code == 200
        exported = export_resp.json()["config"]
        assert exported["field_mappings"][0]["source_field"] == "pan_number"
        assert exported["field_mappings"][0]["target_field"] == "pan_id"

        with patch("finspark.api.routes.simulations.settings") as mock_settings:
            mock_settings.ai_enabled = False
            mock_settings.gemini_api_key = ""
            mock_settings.openai_api_key = ""
            sim_resp = await client.post(
                "/api/v1/simulations/run",
                json={"configuration_id": config.id, "test_type": "smoke"},
            )
        assert sim_resp.status_code == 200
        endpoint_step = next(
            step
            for step in sim_resp.json()["data"]["steps"]
            if step["step_name"].startswith("endpoint_test_")
        )
        assert endpoint_step["request_payload"] == {"pan_number": "ABCDE1234F"}

    async def test_patch_updates_name(self, client, db_session) -> None:
        config = _make_config()
        db_session.add(config)
        await db_session.flush()

        resp = await client.patch(
            f"/api/v1/configurations/{config.id}",
            json={"name": "Renamed Config"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["name"] == "Renamed Config"

    async def test_patch_returns_404_for_nonexistent_config(self, client, db_session) -> None:
        resp = await client.patch(
            f"/api/v1/configurations/{uuid.uuid4()}",
            json={"name": "Ghost"},
        )
        assert resp.status_code == 404

    async def test_patch_partial_update_leaves_other_fields_unchanged(
        self, client, db_session
    ) -> None:
        config = _make_config(name="Original Name")
        db_session.add(config)
        await db_session.flush()

        original_mappings_count = len(json.loads(config.field_mappings))

        # Only update name — field_mappings must remain intact
        resp = await client.patch(
            f"/api/v1/configurations/{config.id}",
            json={"name": "New Name Only"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["name"] == "New Name Only"
        assert len(data["data"]["field_mappings"]) == original_mappings_count
