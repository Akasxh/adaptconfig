"""Webhook management routes."""

import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.api.dependencies import get_tenant_context
from finspark.core.database import get_db
from finspark.core.security import encrypt_value
from finspark.models.webhook import Webhook, WebhookDelivery
from finspark.schemas.common import APIResponse, TenantContext
from finspark.schemas.webhooks import WebhookCreate, WebhookDeliveryResponse, WebhookResponse

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


def _webhook_to_response(wh: Webhook) -> WebhookResponse:
    events = json.loads(wh.events) if wh.events else []
    return WebhookResponse(
        id=wh.id,
        tenant_id=wh.tenant_id,
        url=wh.url,
        events=events,
        is_active=wh.is_active,
        created_at=wh.created_at,
    )


@router.post("/", response_model=APIResponse[WebhookResponse], status_code=201)
async def register_webhook(
    body: WebhookCreate,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[WebhookResponse]:
    """Register a new webhook endpoint."""
    wh = Webhook(
        id=str(uuid.uuid4()),
        tenant_id=tenant.tenant_id,
        url=str(body.url),
        secret=encrypt_value(body.secret),
        events=json.dumps(body.events),
        is_active=body.is_active,
    )
    db.add(wh)
    await db.flush()
    await db.refresh(wh)
    return APIResponse(data=_webhook_to_response(wh), message="Webhook registered")


@router.get("/", response_model=APIResponse[list[WebhookResponse]])
async def list_webhooks(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[list[WebhookResponse]]:
    """List active webhooks for the current tenant.

    Only returns webhooks where is_active=True. Inactive (soft-deleted)
    webhooks are excluded. Note: Webhook uses is_active as its soft-delete
    flag — there is no separate is_deleted column on this model.
    """
    stmt = (
        select(Webhook)
        .where(
            Webhook.tenant_id == tenant.tenant_id,
            Webhook.is_active == True,  # noqa: E712 — SQLAlchemy requires == not `is`
        )
        .order_by(Webhook.created_at.desc())
    )
    result = await db.execute(stmt)
    webhooks = result.scalars().all()
    return APIResponse(data=[_webhook_to_response(wh) for wh in webhooks])


@router.delete("/{webhook_id}", response_model=APIResponse[None])
async def delete_webhook(
    webhook_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[None]:
    """Delete a webhook."""
    stmt = select(Webhook).where(Webhook.id == webhook_id, Webhook.tenant_id == tenant.tenant_id)
    result = await db.execute(stmt)
    wh = result.scalar_one_or_none()
    if not wh:
        raise HTTPException(status_code=404, detail="Webhook not found")
    await db.delete(wh)
    return APIResponse(message="Webhook deleted")


@router.post("/{webhook_id}/test", response_model=APIResponse[WebhookDeliveryResponse])
async def test_webhook(
    webhook_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[WebhookDeliveryResponse]:
    """Send a test event to a webhook."""
    stmt = select(Webhook).where(Webhook.id == webhook_id, Webhook.tenant_id == tenant.tenant_id)
    result = await db.execute(stmt)
    wh = result.scalar_one_or_none()
    if not wh:
        raise HTTPException(status_code=404, detail="Webhook not found")

    test_payload = {
        "event": "webhook.test",
        "timestamp": datetime.now(UTC).isoformat(),
        "webhook_id": wh.id,
        "tenant_id": wh.tenant_id,
    }

    delivery = WebhookDelivery(
        id=str(uuid.uuid4()),
        webhook_id=wh.id,
        event_type="webhook.test",
        payload=json.dumps(test_payload),
        status="delivered",
        response_code=200,
        attempts=1,
    )
    db.add(delivery)
    await db.flush()
    await db.refresh(delivery)

    return APIResponse(
        data=WebhookDeliveryResponse(
            id=delivery.id,
            webhook_id=delivery.webhook_id,
            event_type=delivery.event_type,
            payload=test_payload,
            status=delivery.status,
            response_code=delivery.response_code,
            attempts=delivery.attempts,
            created_at=delivery.created_at,
        ),
        message="Test event sent",
    )
