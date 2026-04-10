"""Webhook management routes."""

import json
import logging
import time
import uuid
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.api.dependencies import get_audit_service, get_tenant_context, require_role
from finspark.core.audit import AuditService
from finspark.core.database import get_db
from finspark.core.security import encrypt_value
from finspark.core.url_validator import is_safe_url
from finspark.models.webhook import Webhook, WebhookDelivery
from finspark.schemas.common import APIResponse, TenantContext
from finspark.schemas.webhooks import WebhookCreate, WebhookDeliveryResponse, WebhookResponse

logger = logging.getLogger(__name__)

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
    tenant: TenantContext = require_role("admin", "editor"),
    audit: AuditService = Depends(get_audit_service),
) -> APIResponse[WebhookResponse]:
    """Register a new webhook endpoint."""
    if not is_safe_url(str(body.url)):
        raise HTTPException(status_code=400, detail="URL resolves to a blocked or private network address")

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
    await audit.log(
        tenant_id=tenant.tenant_id,
        actor=tenant.tenant_name,
        action="register_webhook",
        resource_type="webhook",
        resource_id=wh.id,
        details={"url": str(body.url), "events": body.events},
    )
    return APIResponse(data=_webhook_to_response(wh), message="Webhook registered")


@router.get("/", response_model=APIResponse[list[WebhookResponse]])
async def list_webhooks(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
    page: int | None = Query(None, ge=1, description="Page number (1-based). Omit for all results."),
    page_size: int | None = Query(None, ge=1, le=200, description="Items per page. Omit for all results."),
) -> APIResponse[list[WebhookResponse]]:
    """List active webhooks for the current tenant.

    Only returns webhooks where is_active=True. Inactive (soft-deleted)
    webhooks are excluded. Note: Webhook uses is_active as its soft-delete
    flag -- there is no separate is_deleted column on this model.
    """
    stmt = (
        select(Webhook)
        .where(
            Webhook.tenant_id == tenant.tenant_id,
            Webhook.is_active == True,  # noqa: E712
        )
        .order_by(Webhook.created_at.desc())
    )
    if page is not None and page_size is not None:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    webhooks = result.scalars().all()
    return APIResponse(data=[_webhook_to_response(wh) for wh in webhooks])


@router.delete("/{webhook_id}", response_model=APIResponse[None])
async def delete_webhook(
    webhook_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = require_role("admin", "editor"),
    audit: AuditService = Depends(get_audit_service),
) -> APIResponse[None]:
    """Delete a webhook."""
    stmt = select(Webhook).where(Webhook.id == webhook_id, Webhook.tenant_id == tenant.tenant_id)
    result = await db.execute(stmt)
    wh = result.scalar_one_or_none()
    if not wh:
        raise HTTPException(status_code=404, detail="Webhook not found")
    await db.delete(wh)
    await audit.log(
        tenant_id=tenant.tenant_id,
        actor=tenant.tenant_name,
        action="delete_webhook",
        resource_type="webhook",
        resource_id=webhook_id,
        details={},
    )
    return APIResponse(message="Webhook deleted")


@router.post("/{webhook_id}/test", response_model=APIResponse[WebhookDeliveryResponse])
async def test_webhook(
    webhook_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
    audit: AuditService = Depends(get_audit_service),
) -> APIResponse[WebhookDeliveryResponse]:
    """Send a test event to a webhook."""
    stmt = select(Webhook).where(Webhook.id == webhook_id, Webhook.tenant_id == tenant.tenant_id)
    result = await db.execute(stmt)
    wh = result.scalar_one_or_none()
    if not wh:
        raise HTTPException(status_code=404, detail="Webhook not found")

    if not is_safe_url(wh.url):
        raise HTTPException(status_code=400, detail="Webhook URL resolves to a blocked or private network address")

    test_payload = {
        "event": "webhook.test",
        "timestamp": time.time(),
        "webhook_id": wh.id,
        "tenant_id": wh.tenant_id,
    }
    body = json.dumps(test_payload)

    status = "failed"
    response_code: int | None = None

    try:
        async with httpx.AsyncClient(timeout=5.0) as http_client:
            resp = await http_client.post(
                wh.url,
                content=body,
                headers={"Content-Type": "application/json"},
            )
            response_code = resp.status_code
            if 200 <= resp.status_code < 300:
                status = "delivered"
    except httpx.RequestError:
        status = "failed"

    delivery = WebhookDelivery(
        id=str(uuid.uuid4()),
        webhook_id=wh.id,
        event_type="webhook.test",
        payload=body,
        status=status,
        response_code=response_code,
        attempts=1,
    )
    db.add(delivery)
    await db.flush()
    await db.refresh(delivery)
    await audit.log(
        tenant_id=tenant.tenant_id,
        actor=tenant.tenant_name,
        action="test_webhook",
        resource_type="webhook",
        resource_id=webhook_id,
        details={"status": delivery.status, "response_code": delivery.response_code},
    )

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
