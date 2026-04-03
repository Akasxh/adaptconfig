"""Unit tests for the POST /{config_id}/transition endpoint."""

import json
import uuid

import pytest

from finspark.models.configuration import Configuration


def _make_config(status: str = "configured") -> Configuration:
    return Configuration(
        id=str(uuid.uuid4()),
        tenant_id="test-tenant",
        name="Transition Test Config",
        adapter_version_id=str(uuid.uuid4()),
        status=status,
        version=1,
        field_mappings=json.dumps([]),
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
class TestTransitionEndpoint:
    async def test_valid_transition_returns_200(self, client, db_session) -> None:
        config = _make_config(status="configured")
        db_session.add(config)
        await db_session.flush()

        resp = await client.post(
            f"/api/v1/configurations/{config.id}/transition",
            json={"target_state": "validating"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["new_state"] == "validating"
        assert data["data"]["previous_state"] == "configured"

    async def test_invalid_transition_returns_400(self, client, db_session) -> None:
        config = _make_config(status="configured")
        db_session.add(config)
        await db_session.flush()

        # configured -> active is not a valid transition
        resp = await client.post(
            f"/api/v1/configurations/{config.id}/transition",
            json={"target_state": "active"},
        )
        assert resp.status_code == 400

    async def test_nonexistent_config_returns_404(self, client) -> None:
        resp = await client.post(
            "/api/v1/configurations/nonexistent-id/transition",
            json={"target_state": "validating"},
        )
        assert resp.status_code == 404

    async def test_transition_from_draft_to_configured(self, client, db_session) -> None:
        config = _make_config(status="draft")
        db_session.add(config)
        await db_session.flush()

        resp = await client.post(
            f"/api/v1/configurations/{config.id}/transition",
            json={"target_state": "configured"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["new_state"] == "configured"
        assert data["data"]["previous_state"] == "draft"

    async def test_transition_response_includes_available_transitions(
        self, client, db_session
    ) -> None:
        config = _make_config(status="configured")
        db_session.add(config)
        await db_session.flush()

        resp = await client.post(
            f"/api/v1/configurations/{config.id}/transition",
            json={"target_state": "validating"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "available_transitions" in data["data"]
        assert isinstance(data["data"]["available_transitions"], list)
