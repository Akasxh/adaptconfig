"""Webhook event delivery service."""

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.core.database import async_session_factory
from finspark.core.json_utils import safe_json_loads
from finspark.core.security import decrypt_value
from finspark.core.url_validator import is_safe_url
from finspark.models.webhook import Webhook, WebhookDelivery

logger = logging.getLogger(__name__)


async def deliver_event(tenant_id: str, event_type: str, payload: dict[str, Any]) -> None:
    """Deliver an event to all matching webhooks for a tenant."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(Webhook).where(
                Webhook.tenant_id == tenant_id,
                Webhook.is_active == True,  # noqa: E712
            )
        )
        webhooks = result.scalars().all()

        matching_webhooks = []
        for wh in webhooks:
            events = safe_json_loads(wh.events, []) if isinstance(wh.events, str) else (wh.events or [])
            if event_type in events or "*" in events:
                matching_webhooks.append(wh)

        tasks = [_send_webhook(db, wh, event_type, payload) for wh in matching_webhooks]
        await asyncio.gather(*tasks, return_exceptions=True)

        await db.commit()


async def _send_webhook(
    db: AsyncSession, webhook: Webhook, event_type: str, payload: dict[str, Any]
) -> None:
    """Send a single webhook delivery with retry."""
    if not is_safe_url(webhook.url):
        logger.warning("Blocked SSRF attempt for webhook %s url=%s", webhook.id, webhook.url)
        return

    body = json.dumps({
        "event": event_type,
        "timestamp": time.time(),
        "webhook_id": webhook.id,
        "data": payload,
    })

    headers: dict[str, str] = {"Content-Type": "application/json"}

    if webhook.secret:
        try:
            secret = decrypt_value(webhook.secret)
            signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            headers["X-Webhook-Signature"] = f"sha256={signature}"
        except Exception:
            logger.warning("Failed to decrypt webhook secret for %s", webhook.id)

    delivery = WebhookDelivery(
        id=str(uuid.uuid4()),
        webhook_id=webhook.id,
        event_type=event_type,
        payload=body,
        status="pending",
        attempts=0,
    )
    db.add(delivery)
    await db.flush()

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        delivery.attempts = attempt
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(webhook.url, content=body, headers=headers)
                delivery.response_code = resp.status_code
                if 200 <= resp.status_code < 300:
                    delivery.status = "delivered"
                    return
        except httpx.RequestError as e:
            logger.warning(
                "Webhook delivery attempt %d/%d failed for %s: %s",
                attempt,
                max_attempts,
                webhook.id,
                str(e),
            )
        if attempt < max_attempts:
            await asyncio.sleep(2 ** attempt)  # 2s, 4s backoff

    delivery.status = "failed"
