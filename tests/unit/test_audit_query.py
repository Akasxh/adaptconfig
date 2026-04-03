"""Tests for audit log query endpoint: filter-consistent count and malformed JSON handling."""

import json
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.core.database import get_db
from finspark.main import app
from finspark.models.audit import AuditLog


def _make_log(
    tenant_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    details: str | None = None,
) -> AuditLog:
    return AuditLog(
        tenant_id=tenant_id,
        actor="test-actor",
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        created_at=datetime.now(timezone.utc),
    )


async def _get_client(db_session: AsyncSession, tenant_id: str = "tenant-test") -> AsyncClient:
    async def override_get_db():  # noqa: ANN202
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    client.headers["X-Tenant-ID"] = tenant_id
    client.headers["X-Tenant-Name"] = "Test Tenant"
    client.headers["X-Tenant-Role"] = "admin"
    return client


class TestAuditCountMatchesFilteredResults:
    """Pagination total must reflect all active query filters."""

    @pytest.mark.asyncio
    async def test_count_without_filters_matches_all_tenant_logs(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add_all([
            _make_log("t1", "create", "configuration", "r1"),
            _make_log("t1", "delete", "adapter", "r2"),
            _make_log("t1", "update", "configuration", "r3"),
        ])
        await db_session.flush()

        client = await _get_client(db_session, "t1")
        try:
            resp = await client.get("/api/v1/audit/")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["total"] == 3
            assert len(data["items"]) == 3
        finally:
            await client.aclose()
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_count_with_action_filter_excludes_other_actions(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add_all([
            _make_log("t1", "create", "configuration", "r1"),
            _make_log("t1", "create", "configuration", "r2"),
            _make_log("t1", "delete", "configuration", "r3"),
        ])
        await db_session.flush()

        client = await _get_client(db_session, "t1")
        try:
            resp = await client.get("/api/v1/audit/", params={"action": "create"})
            assert resp.status_code == 200
            data = resp.json()["data"]
            # total must only count "create" entries, not all 3
            assert data["total"] == 2
            assert len(data["items"]) == 2
            assert all(item["action"] == "create" for item in data["items"])
        finally:
            await client.aclose()
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_count_with_resource_type_filter(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add_all([
            _make_log("t1", "create", "configuration", "r1"),
            _make_log("t1", "create", "adapter", "r2"),
            _make_log("t1", "create", "adapter", "r3"),
        ])
        await db_session.flush()

        client = await _get_client(db_session, "t1")
        try:
            resp = await client.get("/api/v1/audit/", params={"resource_type": "adapter"})
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["total"] == 2
            assert len(data["items"]) == 2
            assert all(item["resource_type"] == "adapter" for item in data["items"])
        finally:
            await client.aclose()
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_count_with_resource_id_filter(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add_all([
            _make_log("t1", "create", "configuration", "target-id"),
            _make_log("t1", "update", "configuration", "target-id"),
            _make_log("t1", "create", "configuration", "other-id"),
        ])
        await db_session.flush()

        client = await _get_client(db_session, "t1")
        try:
            resp = await client.get("/api/v1/audit/", params={"resource_id": "target-id"})
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["total"] == 2
            assert len(data["items"]) == 2
        finally:
            await client.aclose()
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_count_with_combined_filters(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add_all([
            _make_log("t1", "create", "configuration", "r1"),
            _make_log("t1", "create", "adapter", "r1"),
            _make_log("t1", "delete", "configuration", "r1"),
            _make_log("t1", "create", "configuration", "r2"),
        ])
        await db_session.flush()

        client = await _get_client(db_session, "t1")
        try:
            resp = await client.get(
                "/api/v1/audit/",
                params={"action": "create", "resource_type": "configuration"},
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["total"] == 2
            assert len(data["items"]) == 2
        finally:
            await client.aclose()
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_pagination_has_next_reflects_filtered_count(
        self, db_session: AsyncSession
    ) -> None:
        # 5 "create" logs, but 3 "delete" logs (8 total)
        for i in range(5):
            db_session.add(_make_log("t1", "create", "configuration", f"r{i}"))
        for i in range(3):
            db_session.add(_make_log("t1", "delete", "configuration", f"d{i}"))
        await db_session.flush()

        client = await _get_client(db_session, "t1")
        try:
            # Request page 1 of 3 with action=create -> total=5, has_next=True
            resp = await client.get(
                "/api/v1/audit/",
                params={"action": "create", "page": 1, "page_size": 3},
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["total"] == 5
            assert data["has_next"] is True

            # Page 2 of 3 -> has_next=False (3+2=5)
            resp2 = await client.get(
                "/api/v1/audit/",
                params={"action": "create", "page": 2, "page_size": 3},
            )
            data2 = resp2.json()["data"]
            assert data2["total"] == 5
            assert data2["has_next"] is False
        finally:
            await client.aclose()
            app.dependency_overrides.clear()


class TestAuditMalformedJsonDetails:
    """Malformed JSON in audit log details must not crash the endpoint."""

    @pytest.mark.asyncio
    async def test_malformed_json_details_returns_none(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add(
            _make_log("t1", "create", "configuration", "r1", details="{not valid json")
        )
        await db_session.flush()

        client = await _get_client(db_session, "t1")
        try:
            resp = await client.get("/api/v1/audit/")
            assert resp.status_code == 200
            items = resp.json()["data"]["items"]
            assert len(items) == 1
            assert items[0]["details"] is None
        finally:
            await client.aclose()
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_valid_json_details_returned_correctly(
        self, db_session: AsyncSession
    ) -> None:
        payload = {"score": 750, "bureau": "experian"}
        db_session.add(
            _make_log("t1", "create", "configuration", "r1", details=json.dumps(payload))
        )
        await db_session.flush()

        client = await _get_client(db_session, "t1")
        try:
            resp = await client.get("/api/v1/audit/")
            assert resp.status_code == 200
            items = resp.json()["data"]["items"]
            assert items[0]["details"] == payload
        finally:
            await client.aclose()
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_mixed_valid_and_malformed_json_details(
        self, db_session: AsyncSession
    ) -> None:
        valid_payload = {"key": "value"}
        db_session.add_all([
            _make_log("t1", "create", "configuration", "r1", details=json.dumps(valid_payload)),
            _make_log("t1", "update", "configuration", "r2", details="not-json-at-all"),
            _make_log("t1", "delete", "configuration", "r3", details=None),
        ])
        await db_session.flush()

        client = await _get_client(db_session, "t1")
        try:
            resp = await client.get("/api/v1/audit/")
            assert resp.status_code == 200
            items = resp.json()["data"]["items"]
            assert len(items) == 3

            details_by_action = {item["action"]: item["details"] for item in items}
            assert details_by_action["create"] == valid_payload
            assert details_by_action["update"] is None
            assert details_by_action["delete"] is None
        finally:
            await client.aclose()
            app.dependency_overrides.clear()
