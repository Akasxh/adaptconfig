"""Comprehensive tests for AdapterRegistry service to boost coverage."""

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.adapter import Adapter, AdapterVersion
from finspark.services.registry.adapter_registry import AdapterRegistry


class TestListAdapters:
    @pytest.mark.asyncio
    async def test_list_empty(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        result = await registry.list_adapters()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_all_active(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        await registry.create_adapter(name="A1", category="bureau")
        await registry.create_adapter(name="A2", category="kyc")
        result = await registry.list_adapters()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_by_category(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        await registry.create_adapter(name="Bureau1", category="bureau")
        await registry.create_adapter(name="KYC1", category="kyc")
        result = await registry.list_adapters(category="bureau")
        assert len(result) == 1
        assert result[0].category == "bureau"

    @pytest.mark.asyncio
    async def test_list_includes_inactive_when_flag_false(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        adapter = await registry.create_adapter(name="Inactive", category="bureau")
        adapter.is_active = False
        await db_session.flush()

        active_only = await registry.list_adapters(is_active=True)
        assert len(active_only) == 0

        all_adapters = await registry.list_adapters(is_active=False)
        assert len(all_adapters) == 1


class TestGetAdapter:
    @pytest.mark.asyncio
    async def test_get_existing(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        created = await registry.create_adapter(name="Test", category="kyc")
        result = await registry.get_adapter(created.id)
        assert result is not None
        assert result.name == "Test"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        result = await registry.get_adapter("nonexistent-id")
        assert result is None


class TestGetAdapterVersion:
    @pytest.mark.asyncio
    async def test_get_existing_version(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        adapter = await registry.create_adapter(name="A", category="bureau")
        version = await registry.add_version(
            adapter_id=adapter.id,
            version="v1",
            base_url="https://api.example.com",
            auth_type="api_key",
            endpoints=[{"path": "/test", "method": "GET", "description": "test"}],
        )
        result = await registry.get_adapter_version(version.id)
        assert result is not None
        assert result.version == "v1"

    @pytest.mark.asyncio
    async def test_get_nonexistent_version(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        result = await registry.get_adapter_version("nonexistent")
        assert result is None


class TestCreateAdapter:
    @pytest.mark.asyncio
    async def test_create_basic(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        adapter = await registry.create_adapter(
            name="New Adapter",
            category="payment",
            description="A payment adapter",
            icon="wallet",
        )
        assert adapter.name == "New Adapter"
        assert adapter.category == "payment"
        assert adapter.description == "A payment adapter"
        assert adapter.icon == "wallet"

    @pytest.mark.asyncio
    async def test_create_minimal(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        adapter = await registry.create_adapter(name="Min", category="gst")
        assert adapter.name == "Min"
        assert adapter.description == ""


class TestAddVersion:
    @pytest.mark.asyncio
    async def test_add_first_version(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        adapter = await registry.create_adapter(name="A", category="bureau")
        v = await registry.add_version(
            adapter_id=adapter.id,
            version="v1",
            base_url="https://api.example.com",
            auth_type="api_key",
            endpoints=[{"path": "/test", "method": "GET", "description": "test"}],
            request_schema={"type": "object"},
            response_schema={"type": "object"},
            config_template={"key": "value"},
            changelog="Initial release",
        )
        assert v.version == "v1"
        assert v.version_order == 1
        assert v.changelog == "Initial release"
        assert json.loads(v.request_schema) == {"type": "object"}

    @pytest.mark.asyncio
    async def test_add_multiple_versions_increments_order(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        adapter = await registry.create_adapter(name="A", category="bureau")
        v1 = await registry.add_version(
            adapter_id=adapter.id,
            version="v1",
            base_url="https://api.example.com/v1",
            auth_type="api_key",
            endpoints=[],
        )
        v2 = await registry.add_version(
            adapter_id=adapter.id,
            version="v2",
            base_url="https://api.example.com/v2",
            auth_type="oauth2",
            endpoints=[],
        )
        assert v1.version_order == 1
        assert v2.version_order == 2

    @pytest.mark.asyncio
    async def test_add_version_no_optional_schemas(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        adapter = await registry.create_adapter(name="A", category="bureau")
        v = await registry.add_version(
            adapter_id=adapter.id,
            version="v1",
            base_url="https://api.example.com",
            auth_type="api_key",
            endpoints=[],
        )
        assert v.request_schema is None
        assert v.response_schema is None
        assert v.config_template is None


class TestDeprecateVersion:
    @pytest.mark.asyncio
    async def test_deprecate_existing(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        adapter = await registry.create_adapter(name="A", category="bureau")
        v = await registry.add_version(
            adapter_id=adapter.id,
            version="v1",
            base_url="https://api.example.com",
            auth_type="api_key",
            endpoints=[],
        )
        result = await registry.deprecate_version(v.id)
        assert result is not None
        assert result.status == "deprecated"

    @pytest.mark.asyncio
    async def test_deprecate_nonexistent(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        result = await registry.deprecate_version("nonexistent")
        assert result is None


class TestFindMatchingAdapters:
    @pytest.mark.asyncio
    async def test_find_by_name(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        await registry.create_adapter(name="CIBIL Credit Bureau", category="bureau")
        await registry.create_adapter(name="Payment Gateway", category="payment")

        matched = await registry.find_matching_adapters(["credit"])
        assert len(matched) == 1
        assert matched[0].name == "CIBIL Credit Bureau"

    @pytest.mark.asyncio
    async def test_find_by_category(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        await registry.create_adapter(name="Some Adapter", category="bureau")

        matched = await registry.find_matching_adapters(["bureau"])
        assert len(matched) == 1

    @pytest.mark.asyncio
    async def test_find_by_description(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        await registry.create_adapter(
            name="KYC Provider",
            category="kyc",
            description="Aadhaar-based electronic KYC verification",
        )

        matched = await registry.find_matching_adapters(["aadhaar"])
        assert len(matched) == 1

    @pytest.mark.asyncio
    async def test_find_no_match(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        await registry.create_adapter(name="Bureau", category="bureau")

        matched = await registry.find_matching_adapters(["nonexistent"])
        assert len(matched) == 0


class TestGetCategories:
    @pytest.mark.asyncio
    async def test_get_categories(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        await registry.create_adapter(name="A1", category="bureau")
        await registry.create_adapter(name="A2", category="kyc")
        await registry.create_adapter(name="A3", category="bureau")

        categories = await registry.get_categories()
        assert set(categories) == {"bureau", "kyc"}

    @pytest.mark.asyncio
    async def test_get_categories_empty(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        categories = await registry.get_categories()
        assert categories == []


class TestGetAdapterByName:
    @pytest.mark.asyncio
    async def test_get_by_name(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        await registry.create_adapter(name="My Adapter", category="kyc")
        result = await registry.get_adapter_by_name("My Adapter")
        assert result is not None
        assert result.name == "My Adapter"

    @pytest.mark.asyncio
    async def test_get_by_name_not_found(self, db_session: AsyncSession) -> None:
        registry = AdapterRegistry(db_session)
        result = await registry.get_adapter_by_name("Nonexistent")
        assert result is None
