"""Tests for soft-delete filtering in route queries (Issue #34).

Audit of is_deleted / is_active filtering across route files:

- Webhook: uses is_active=True as the soft-delete flag. list_webhooks now
  filters WHERE is_active = True.
- Document: no is_deleted column; delete_document does a hard db.delete().
- Configuration: no is_deleted column; queries scoped by tenant_id only.
- Adapter: no is_deleted column; uses is_active but queries go through
  AdapterRegistry service, not direct SELECT in routes.

Models with SoftDeleteMixin (is_deleted + deleted_at) live in
backend/src/finspark/models/ which is a separate package with its own DB.
Those models are NOT queried in src/finspark/api/routes/. Consequently no
additional is_deleted filters are needed there.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.webhook import Webhook


class TestWebhookIsActiveFiltering:
    """list_webhooks must exclude is_active=False webhooks."""

    @pytest.mark.asyncio
    async def test_list_excludes_inactive_webhooks(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Inactive webhooks must not appear in GET /webhooks/."""
        # Create an active webhook via API
        resp = await client.post(
            "/api/v1/webhooks/",
            json={"url": "https://active.example.com/hook", "secret": "s1", "events": ["a"]},
        )
        assert resp.status_code == 201
        active_id = resp.json()["data"]["id"]

        # Directly insert an inactive webhook (simulates a soft-deleted record)
        import uuid

        inactive_wh = Webhook(
            id=str(uuid.uuid4()),
            tenant_id="test-tenant",
            url="https://inactive.example.com/hook",
            secret="encrypted-secret",
            events='["b"]',
            is_active=False,
        )
        db_session.add(inactive_wh)
        await db_session.flush()

        list_resp = await client.get("/api/v1/webhooks/")
        assert list_resp.status_code == 200
        data = list_resp.json()["data"]

        ids = [wh["id"] for wh in data]
        assert active_id in ids
        assert inactive_wh.id not in ids

    @pytest.mark.asyncio
    async def test_list_returns_only_active(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Only active webhooks are returned even when multiple inactive exist."""
        import uuid

        # Insert 2 inactive webhooks directly
        for _ in range(2):
            wh = Webhook(
                id=str(uuid.uuid4()),
                tenant_id="test-tenant",
                url=f"https://inactive-{uuid.uuid4()}.example.com/hook",
                secret="s",
                events="[]",
                is_active=False,
            )
            db_session.add(wh)
        await db_session.flush()

        # Create 1 active webhook
        resp = await client.post(
            "/api/v1/webhooks/",
            json={"url": "https://active.example.com/hook", "secret": "s", "events": []},
        )
        assert resp.status_code == 201

        list_resp = await client.get("/api/v1/webhooks/")
        assert list_resp.status_code == 200
        data = list_resp.json()["data"]
        assert len(data) == 1
        assert data[0]["is_active"] is True

    @pytest.mark.asyncio
    async def test_list_empty_when_all_inactive(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Empty list is returned when all webhooks for tenant are inactive."""
        import uuid

        wh = Webhook(
            id=str(uuid.uuid4()),
            tenant_id="test-tenant",
            url="https://example.com/hook",
            secret="s",
            events="[]",
            is_active=False,
        )
        db_session.add(wh)
        await db_session.flush()

        list_resp = await client.get("/api/v1/webhooks/")
        assert list_resp.status_code == 200
        assert list_resp.json()["data"] == []

    @pytest.mark.asyncio
    async def test_active_webhook_is_visible(self, client: AsyncClient) -> None:
        """Active webhooks must appear in the list."""
        await client.post(
            "/api/v1/webhooks/",
            json={"url": "https://example.com/hook", "secret": "s", "events": ["x"]},
        )
        list_resp = await client.get("/api/v1/webhooks/")
        assert list_resp.status_code == 200
        assert len(list_resp.json()["data"]) == 1

    @pytest.mark.asyncio
    async def test_tenant_isolation_with_inactive(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Inactive webhooks from another tenant must not appear."""
        import uuid

        other_tenant_wh = Webhook(
            id=str(uuid.uuid4()),
            tenant_id="other-tenant",
            url="https://other.example.com/hook",
            secret="s",
            events="[]",
            is_active=True,  # active but different tenant
        )
        db_session.add(other_tenant_wh)
        await db_session.flush()

        list_resp = await client.get("/api/v1/webhooks/")
        assert list_resp.status_code == 200
        ids = [wh["id"] for wh in list_resp.json()["data"]]
        assert other_tenant_wh.id not in ids
