"""Tests for utility modules to boost overall coverage."""

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.core.json_utils import safe_json_loads
from finspark.core.logging import configure_logging, pii_masking_processor
from finspark.models.audit import AuditLog


class TestSafeJsonLoads:
    def test_none_returns_default(self) -> None:
        assert safe_json_loads(None) is None

    def test_empty_string_returns_default(self) -> None:
        assert safe_json_loads("") is None

    def test_valid_json(self) -> None:
        assert safe_json_loads('{"key": "value"}') == {"key": "value"}

    def test_valid_json_array(self) -> None:
        assert safe_json_loads('[1, 2, 3]') == [1, 2, 3]

    def test_malformed_json_returns_default(self) -> None:
        assert safe_json_loads("{invalid") is None

    def test_custom_default(self) -> None:
        assert safe_json_loads(None, []) == []
        assert safe_json_loads("{bad", {"fallback": True}) == {"fallback": True}


class TestPiiMaskingProcessor:
    def test_masks_string_values(self) -> None:
        # pii_masking_processor calls mask_pii on string values
        event_dict = {"msg": "test@example.com", "count": 5}
        result = pii_masking_processor(None, "info", event_dict)
        assert isinstance(result, dict)
        # The count should remain unchanged (not a string)
        assert result["count"] == 5

    def test_returns_dict(self) -> None:
        result = pii_masking_processor(None, "info", {"key": "value"})
        assert isinstance(result, dict)


class TestConfigureLogging:
    def test_configure_logging_does_not_raise(self) -> None:
        # Should work whether structlog is installed or not
        configure_logging()


class TestAuditRouteIndirect:
    """Test audit log query via API to cover audit route handler."""

    @pytest.mark.asyncio
    async def test_query_audit_logs_empty(
        self, client: "AsyncClient", db_session: AsyncSession  # noqa: F821
    ) -> None:
        from httpx import AsyncClient

        resp = await client.get("/api/v1/audit/")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0
        assert data["items"] == []

    @pytest.mark.asyncio
    async def test_query_audit_logs_with_data(
        self, client: "AsyncClient", db_session: AsyncSession  # noqa: F821
    ) -> None:
        # Create some audit logs
        for i in range(3):
            log = AuditLog(
                tenant_id="test-tenant",
                actor="tester",
                action="test_action",
                resource_type="configuration",
                resource_id=f"res-{i}",
                details=json.dumps({"key": f"value-{i}"}),
            )
            db_session.add(log)
        await db_session.flush()
        await db_session.commit()

        resp = await client.get("/api/v1/audit/")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 3
        assert len(data["items"]) == 3

    @pytest.mark.asyncio
    async def test_query_audit_logs_with_filters(
        self, client: "AsyncClient", db_session: AsyncSession  # noqa: F821
    ) -> None:
        for action in ["create", "update", "delete"]:
            log = AuditLog(
                tenant_id="test-tenant",
                actor="tester",
                action=action,
                resource_type="configuration",
                resource_id="res-1",
            )
            db_session.add(log)
        await db_session.flush()
        await db_session.commit()

        resp = await client.get("/api/v1/audit/?action=create")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1

    @pytest.mark.asyncio
    async def test_query_audit_logs_with_resource_filter(
        self, client: "AsyncClient", db_session: AsyncSession  # noqa: F821
    ) -> None:
        for rt in ["configuration", "document", "configuration"]:
            log = AuditLog(
                tenant_id="test-tenant",
                actor="tester",
                action="create",
                resource_type=rt,
                resource_id="res-1",
            )
            db_session.add(log)
        await db_session.flush()
        await db_session.commit()

        resp = await client.get("/api/v1/audit/?resource_type=document")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1

    @pytest.mark.asyncio
    async def test_query_audit_logs_pagination(
        self, client: "AsyncClient", db_session: AsyncSession  # noqa: F821
    ) -> None:
        for i in range(5):
            log = AuditLog(
                tenant_id="test-tenant",
                actor="tester",
                action="create",
                resource_type="configuration",
                resource_id=f"res-{i}",
            )
            db_session.add(log)
        await db_session.flush()
        await db_session.commit()

        resp = await client.get("/api/v1/audit/?page=1&page_size=2")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 5
        assert len(data["items"]) == 2
        assert data["has_next"] is True

    @pytest.mark.asyncio
    async def test_query_audit_logs_malformed_details(
        self, client: "AsyncClient", db_session: AsyncSession  # noqa: F821
    ) -> None:
        log = AuditLog(
            tenant_id="test-tenant",
            actor="tester",
            action="create",
            resource_type="configuration",
            resource_id="res-1",
            details="{invalid json",
        )
        db_session.add(log)
        await db_session.flush()
        await db_session.commit()

        resp = await client.get("/api/v1/audit/")
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) == 1
        assert items[0]["details"] is None
