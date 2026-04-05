"""Integration tests for configuration routes to boost coverage."""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.adapter import Adapter, AdapterVersion
from finspark.models.configuration import Configuration, ConfigurationHistory
from finspark.models.document import Document
from finspark.services.registry.adapter_registry import AdapterRegistry


TENANT = "test-tenant"


async def _create_adapter_version(db: AsyncSession) -> str:
    """Create an adapter + version, return version_id."""
    registry = AdapterRegistry(db)
    adapter = await registry.create_adapter(name="Bureau", category="bureau")
    v = await registry.add_version(
        adapter_id=adapter.id,
        version="v1",
        base_url="https://api.example.com",
        auth_type="api_key",
        endpoints=[{"path": "/test", "method": "POST", "description": "test"}],
    )
    await db.flush()
    return v.id


async def _create_config(
    db: AsyncSession,
    av_id: str,
    name: str = "Test Config",
    status: str = "configured",
    full_config: dict | None = None,
    field_mappings: list | None = None,
) -> Configuration:
    """Create a configuration in the DB."""
    if full_config is None:
        full_config = {
            "base_url": "https://api.example.com",
            "auth": {"type": "api_key"},
            "endpoints": [{"path": "/test", "method": "POST"}],
            "field_mappings": field_mappings or [
                {"source_field": "pan", "target_field": "pan_number", "confidence": 0.9},
                {"source_field": "name", "target_field": "full_name", "confidence": 0.8},
            ],
        }
    cfg = Configuration(
        tenant_id=TENANT,
        name=name,
        adapter_version_id=av_id,
        status=status,
        version=1,
        full_config=json.dumps(full_config),
        field_mappings=json.dumps(field_mappings or full_config.get("field_mappings", [])),
    )
    db.add(cfg)
    await db.flush()
    return cfg


class TestBatchValidate:
    @pytest.mark.asyncio
    async def test_batch_validate_valid(self, client: AsyncClient, db_session: AsyncSession) -> None:
        av_id = await _create_adapter_version(db_session)
        cfg = await _create_config(db_session, av_id)
        await db_session.commit()

        resp = await client.post(
            "/api/v1/configurations/batch-validate",
            json={"config_ids": [cfg.id]},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["result"]["is_valid"] is True

    @pytest.mark.asyncio
    async def test_batch_validate_missing_config(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/configurations/batch-validate",
            json={"config_ids": ["nonexistent"]},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data[0]["error"] == "Configuration not found"

    @pytest.mark.asyncio
    async def test_batch_validate_invalid_config(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        av_id = await _create_adapter_version(db_session)
        cfg = await _create_config(
            db_session,
            av_id,
            full_config={"field_mappings": []},  # missing base_url, auth, endpoints
        )
        await db_session.commit()

        resp = await client.post(
            "/api/v1/configurations/batch-validate",
            json={"config_ids": [cfg.id]},
        )
        assert resp.status_code == 200
        result = resp.json()["data"][0]["result"]
        assert result["is_valid"] is False
        assert "Missing base_url" in result["errors"]

    @pytest.mark.asyncio
    async def test_batch_validate_warnings(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        av_id = await _create_adapter_version(db_session)
        fc = {
            "base_url": "https://api.example.com",
            "auth": {"type": "api_key"},
            "endpoints": [{"path": "/test", "method": "POST"}],
            "field_mappings": [
                {"source_field": "pan", "target_field": "", "confidence": 0.9},
                {"source_field": "name", "target_field": "full_name", "confidence": 0.3},
            ],
        }
        cfg = await _create_config(db_session, av_id, full_config=fc)
        await db_session.commit()

        resp = await client.post(
            "/api/v1/configurations/batch-validate",
            json={"config_ids": [cfg.id]},
        )
        result = resp.json()["data"][0]["result"]
        assert len(result["warnings"]) >= 1


class TestBatchSimulate:
    @pytest.mark.asyncio
    async def test_batch_simulate(self, client: AsyncClient, db_session: AsyncSession) -> None:
        av_id = await _create_adapter_version(db_session)
        cfg = await _create_config(db_session, av_id)
        await db_session.commit()

        resp = await client.post(
            "/api/v1/configurations/batch-simulate",
            json={"config_ids": [cfg.id]},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["config_id"] == cfg.id
        assert data[0]["status"] in ("passed", "failed")

    @pytest.mark.asyncio
    async def test_batch_simulate_missing(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/configurations/batch-simulate",
            json={"config_ids": ["nonexistent"]},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data[0]["error"] == "Configuration not found"


class TestConfigurationSummary:
    @pytest.mark.asyncio
    async def test_summary_empty(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/configurations/summary")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0
        assert data["avg_confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_summary_with_data(self, client: AsyncClient, db_session: AsyncSession) -> None:
        av_id = await _create_adapter_version(db_session)
        await _create_config(db_session, av_id, name="C1", status="active")
        await _create_config(db_session, av_id, name="C2", status="draft")
        await db_session.commit()

        resp = await client.get("/api/v1/configurations/summary")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 2
        assert "active" in data["by_status"]


class TestGetSingleConfiguration:
    @pytest.mark.asyncio
    async def test_get_existing(self, client: AsyncClient, db_session: AsyncSession) -> None:
        av_id = await _create_adapter_version(db_session)
        cfg = await _create_config(db_session, av_id)
        await db_session.commit()

        resp = await client.get(f"/api/v1/configurations/{cfg.id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == cfg.id
        assert data["name"] == "Test Config"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/configurations/nonexistent")
        assert resp.status_code == 404


class TestValidateConfiguration:
    @pytest.mark.asyncio
    async def test_validate_valid(self, client: AsyncClient, db_session: AsyncSession) -> None:
        av_id = await _create_adapter_version(db_session)
        cfg = await _create_config(db_session, av_id)
        await db_session.commit()

        resp = await client.post(f"/api/v1/configurations/{cfg.id}/validate")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["is_valid"] is True

    @pytest.mark.asyncio
    async def test_validate_invalid(self, client: AsyncClient, db_session: AsyncSession) -> None:
        av_id = await _create_adapter_version(db_session)
        cfg = await _create_config(
            db_session, av_id, full_config={"field_mappings": []}
        )
        await db_session.commit()

        resp = await client.post(f"/api/v1/configurations/{cfg.id}/validate")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["is_valid"] is False

    @pytest.mark.asyncio
    async def test_validate_not_found(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/configurations/nonexistent/validate")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_validate_with_warnings(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        av_id = await _create_adapter_version(db_session)
        fc = {
            "base_url": "https://api.example.com",
            "auth": {"type": "api_key"},
            "endpoints": [{"path": "/test", "method": "POST"}],
            "field_mappings": [
                {"source_field": "pan", "target_field": ""},  # unmapped
                {"source_field": "name", "target_field": "full_name", "confidence": 0.2},  # low conf
            ],
        }
        cfg = await _create_config(db_session, av_id, full_config=fc)
        await db_session.commit()

        resp = await client.post(f"/api/v1/configurations/{cfg.id}/validate")
        data = resp.json()["data"]
        assert data["is_valid"] is True  # no errors, just warnings
        assert len(data["warnings"]) >= 2
        assert len(data["unmapped_source_fields"]) == 1


class TestTransitionConfiguration:
    @pytest.mark.asyncio
    async def test_transition_success(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        av_id = await _create_adapter_version(db_session)
        cfg = await _create_config(db_session, av_id, status="configured")
        await db_session.commit()

        resp = await client.post(
            f"/api/v1/configurations/{cfg.id}/transition",
            json={"target_state": "validating", "reason": "Ready for validation"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["previous_state"] == "configured"
        assert data["new_state"] == "validating"

    @pytest.mark.asyncio
    async def test_transition_invalid(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        av_id = await _create_adapter_version(db_session)
        cfg = await _create_config(db_session, av_id, status="draft")
        await db_session.commit()

        resp = await client.post(
            f"/api/v1/configurations/{cfg.id}/transition",
            json={"target_state": "active"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_transition_not_found(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/configurations/nonexistent/transition",
            json={"target_state": "testing"},
        )
        assert resp.status_code == 404


class TestCompareConfigurations:
    @pytest.mark.asyncio
    async def test_diff_two_configs(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        av_id = await _create_adapter_version(db_session)
        cfg_a = await _create_config(
            db_session,
            av_id,
            name="A",
            full_config={
                "base_url": "https://a.com",
                "auth": {"type": "api_key"},
                "endpoints": [],
                "field_mappings": [],
            },
        )
        cfg_b = await _create_config(
            db_session,
            av_id,
            name="B",
            full_config={
                "base_url": "https://b.com",
                "auth": {"type": "oauth2"},
                "endpoints": [],
                "field_mappings": [],
            },
        )
        await db_session.commit()

        resp = await client.get(f"/api/v1/configurations/{cfg_a.id}/diff/{cfg_b.id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_changes"] > 0

    @pytest.mark.asyncio
    async def test_diff_not_found(self, client: AsyncClient, db_session: AsyncSession) -> None:
        av_id = await _create_adapter_version(db_session)
        cfg = await _create_config(db_session, av_id)
        await db_session.commit()

        resp = await client.get(f"/api/v1/configurations/{cfg.id}/diff/nonexistent")
        assert resp.status_code == 404


class TestListConfigurations:
    @pytest.mark.asyncio
    async def test_list_empty(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/configurations/")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.asyncio
    async def test_list_with_data(self, client: AsyncClient, db_session: AsyncSession) -> None:
        av_id = await _create_adapter_version(db_session)
        await _create_config(db_session, av_id, name="C1")
        await _create_config(db_session, av_id, name="C2")
        await db_session.commit()

        resp = await client.get("/api/v1/configurations/")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_list_with_pagination(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        av_id = await _create_adapter_version(db_session)
        for i in range(5):
            await _create_config(db_session, av_id, name=f"C{i}")
        await db_session.commit()

        resp = await client.get("/api/v1/configurations/?page=1&page_size=2")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2

        resp2 = await client.get("/api/v1/configurations/?page=3&page_size=2")
        data2 = resp2.json()["data"]
        assert len(data2) == 1
