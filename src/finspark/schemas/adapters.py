"""Schemas for integration adapters."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from finspark.schemas.common import AdapterCategory


class AdapterEndpoint(BaseModel):
    """Single endpoint definition within an adapter."""

    path: str
    method: str
    description: str = ""
    request_fields: list[dict[str, Any]] = []
    response_fields: list[dict[str, Any]] = []


class AdapterVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    version: str
    status: str
    auth_type: str
    base_url: str | None = None
    endpoints: list[AdapterEndpoint] = []
    changelog: str | None = None


class AdapterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    category: AdapterCategory
    description: str | None = None
    is_active: bool
    icon: str | None = None
    versions: list[AdapterVersionResponse] = []
    created_at: datetime


class AdapterListResponse(BaseModel):
    adapters: list[AdapterResponse]
    total: int
    categories: list[str] = []


class MigrationStep(BaseModel):
    action: str
    description: str


class DeprecationInfoResponse(BaseModel):
    version: str
    status: str
    sunset_date: str | None = None
    days_until_sunset: int | None = None
    replacement_version: str | None = None
    migration_guide: list[MigrationStep] = []


class AdapterMatch(BaseModel):
    """A single ranked adapter suggestion for a parsed document."""

    adapter_id: str
    version_id: str
    adapter_name: str = ""
    version: str = ""
    score: float = 0.0
    reason: str = ""


class AdapterSuggestRequest(BaseModel):
    """Request body for POST /adapters/suggest."""

    document_id: str


class AdapterSuggestResponse(BaseModel):
    """Top-3 adapter matches plus a flag to fall back to a custom adapter."""

    matches: list[AdapterMatch] = []
    suggest_custom: bool = False
