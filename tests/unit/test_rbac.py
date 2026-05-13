"""Tests for role-based access control enforcement on mutation endpoints."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.core.database import get_db
from finspark.main import app


@pytest_asyncio.fixture
async def admin_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Client authenticated as admin."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.headers["X-Tenant-ID"] = "test-tenant"
        ac.headers["X-Tenant-Name"] = "Admin User"
        ac.headers["X-Tenant-Role"] = "admin"
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def viewer_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Client authenticated as viewer (read-only role)."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.headers["X-Tenant-ID"] = "test-tenant"
        ac.headers["X-Tenant-Name"] = "Viewer User"
        ac.headers["X-Tenant-Role"] = "viewer"
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_viewer_gets_403_on_generate_config(viewer_client: AsyncClient) -> None:
    """Viewer role must not be able to generate configurations."""
    payload = {
        "name": "test-config",
        "document_id": "some-doc-id",
        "adapter_version_id": "some-adapter-id",
    }
    response = await viewer_client.post("/api/v1/configurations/generate", json=payload)
    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient permissions"


@pytest.mark.asyncio
async def test_viewer_can_list_configurations(viewer_client: AsyncClient) -> None:
    """Viewer role must be able to list configurations (read-only)."""
    response = await viewer_client.get("/api/v1/configurations/")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_admin_can_generate_config_request_reaches_handler(admin_client: AsyncClient) -> None:
    """Admin role passes RBAC check; business-logic 404 confirms handler was reached."""
    payload = {
        "name": "test-config",
        "document_id": "nonexistent-doc",
        "adapter_version_id": "nonexistent-adapter",
    }
    response = await admin_client.post("/api/v1/configurations/generate", json=payload)
    # 403 would mean RBAC blocked it; 404 means the handler ran and couldn't find the doc
    assert response.status_code != 403, "Admin should not be blocked by RBAC"
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_viewer_gets_403_on_upload_document(viewer_client: AsyncClient) -> None:
    """Viewer role must not be able to upload documents."""
    response = await viewer_client.post(
        "/api/v1/documents/upload",
        files={"file": ("test.pdf", b"%PDF fake content", "application/pdf")},
        params={"doc_type": "brd"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_viewer_gets_403_on_register_webhook(viewer_client: AsyncClient) -> None:
    """Viewer role must not be able to register webhooks."""
    payload = {
        "url": "https://example.com/hook",
        "secret": "s3cr3t",
        "events": ["config.deployed"],
    }
    response = await viewer_client.post("/api/v1/webhooks/", json=payload)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_editor_can_generate_config(db_session: AsyncSession) -> None:
    """Editor role passes RBAC check on generate; 404 confirms handler ran."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.headers["X-Tenant-ID"] = "test-tenant"
        ac.headers["X-Tenant-Name"] = "Editor User"
        ac.headers["X-Tenant-Role"] = "editor"
        payload = {
            "name": "test-config",
            "document_id": "nonexistent-doc",
            "adapter_version_id": "nonexistent-adapter",
        }
        response = await ac.post("/api/v1/configurations/generate", json=payload)
    app.dependency_overrides.clear()

    assert response.status_code != 403, "Editor should not be blocked by RBAC"
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_editor_gets_403_on_admin_only_rollback(db_session: AsyncSession) -> None:
    """Editor role must be blocked from rollback (admin-only)."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.headers["X-Tenant-ID"] = "test-tenant"
        ac.headers["X-Tenant-Name"] = "Editor User"
        ac.headers["X-Tenant-Role"] = "editor"
        response = await ac.post(
            "/api/v1/configurations/some-config-id/rollback",
            json={"target_version": 1},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 403
