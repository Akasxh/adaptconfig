"""Schemas for webhook management."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, HttpUrl


class WebhookCreate(BaseModel):
    url: HttpUrl
    secret: str
    events: list[str] = []
    is_active: bool = True


class WebhookResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    url: str
    events: list[str]
    is_active: bool
    created_at: datetime


class WebhookDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    webhook_id: str
    event_type: str
    payload: dict
    status: str
    response_code: int | None = None
    attempts: int
    created_at: datetime
