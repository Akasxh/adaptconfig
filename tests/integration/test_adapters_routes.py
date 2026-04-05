"""Integration tests for adapter API routes to boost coverage."""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.services.registry.adapter_registry import AdapterRegistry


async def _create_adapter_with_version(
    db: AsyncSession,
    name: str = "Test Bureau",
    category: str = "bureau",
) -> tuple[str, str]:
    """Create an adapter with a version and return (adapter_id, version_id)."""
    registry = AdapterRegistry(db)
    adapter = await registry.create_adapter(
        name=name,
        category=category,
        description=f"Test {category} adapter",
        icon="test-icon",
    )
    version = await registry.add_version(
        adapter_id=adapter.id,
        version="v1",
        base_url="https://api.example.com/v1",
        auth_type="api_key",
        endpoints=[
            {"path": "/test", "method": "POST", "description": "Test endpoint"},
        ],
        request_schema={"type": "object", "properties": {"field1": {"type": "string"}}},
        response_schema={"type": "object", "properties": {"result": {"type": "string"}}},
    )
    await db.commit()
    return adapter.id, version.id


class TestListAdaptersRoute:
    @pytest.mark.asyncio
    async def test_list_all_adapters(self, client: AsyncClient, db_session: AsyncSession) -> None:
        await _create_adapter_with_version(db_session, "Bureau A", "bureau")
        await _create_adapter_with_version(db_session, "KYC B", "kyc")

        resp = await client.get("/api/v1/adapters/")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 2
        assert len(data["adapters"]) == 2

    @pytest.mark.asyncio
    async def test_list_by_category(self, client: AsyncClient, db_session: AsyncSession) -> None:
        await _create_adapter_with_version(db_session, "Bureau A", "bureau")
        await _create_adapter_with_version(db_session, "KYC B", "kyc")

        resp = await client.get("/api/v1/adapters/?category=bureau")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert data["adapters"][0]["category"] == "bureau"

    @pytest.mark.asyncio
    async def test_list_empty(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/adapters/")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0
        assert data["adapters"] == []

    @pytest.mark.asyncio
    async def test_list_includes_versions(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _create_adapter_with_version(db_session, "Bureau A", "bureau")
        resp = await client.get("/api/v1/adapters/")
        adapter = resp.json()["data"]["adapters"][0]
        assert len(adapter["versions"]) == 1
        assert adapter["versions"][0]["version"] == "v1"

    @pytest.mark.asyncio
    async def test_list_includes_categories(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _create_adapter_with_version(db_session, "Bureau A", "bureau")
        await _create_adapter_with_version(db_session, "KYC B", "kyc")
        resp = await client.get("/api/v1/adapters/")
        data = resp.json()["data"]
        assert set(data["categories"]) == {"bureau", "kyc"}


class TestGetAdapterRoute:
    @pytest.mark.asyncio
    async def test_get_existing(self, client: AsyncClient, db_session: AsyncSession) -> None:
        adapter_id, _ = await _create_adapter_with_version(db_session)
        resp = await client.get(f"/api/v1/adapters/{adapter_id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == adapter_id
        assert data["name"] == "Test Bureau"
        assert len(data["versions"]) == 1

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/adapters/nonexistent-id")
        assert resp.status_code == 404


class TestGetVersionDeprecationRoute:
    @pytest.mark.asyncio
    async def test_deprecation_active_version(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        adapter_id, _ = await _create_adapter_with_version(db_session)
        resp = await client.get(f"/api/v1/adapters/{adapter_id}/versions/v1/deprecation")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["version"] == "v1"
        assert data["status"] in ("active", "ok")

    @pytest.mark.asyncio
    async def test_deprecation_not_found(self, client: AsyncClient, db_session: AsyncSession) -> None:
        adapter_id, _ = await _create_adapter_with_version(db_session)
        resp = await client.get(f"/api/v1/adapters/{adapter_id}/versions/v99/deprecation")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_deprecation_deprecated_version(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        registry = AdapterRegistry(db_session)
        adapter = await registry.create_adapter(name="Dep", category="bureau")
        v1 = await registry.add_version(
            adapter_id=adapter.id,
            version="v1",
            base_url="https://api.example.com",
            auth_type="api_key",
            endpoints=[],
        )
        await registry.add_version(
            adapter_id=adapter.id,
            version="v2",
            base_url="https://api.example.com/v2",
            auth_type="api_key",
            endpoints=[],
        )
        await registry.deprecate_version(v1.id)
        await db_session.commit()

        resp = await client.get(f"/api/v1/adapters/{adapter.id}/versions/v1/deprecation")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "deprecated"
